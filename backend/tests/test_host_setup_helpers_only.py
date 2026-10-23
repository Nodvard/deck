"""Einrichtung nur mit den Regeln der Hilfsskripte (`full_sudo=False`): keine volle sudo-Regel, und was bei einem
Hilfsskript scheitert, ist ein Fehler. Läuft in `sh` gegen die Attrappen aus `test_host_setup_helpers.py`."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from test_host_setup_helpers import SHIELD, USER, _helper, _helper_sandbox
from test_host_setup_script import KEY, _run, _stub, needs_sh

from nodvard_deck.ext.root_helpers import RootHelperFile
from nodvard_deck.services import host_setup
from nodvard_deck.services.host_setup import SetupError, build_setup_script

DOCKER = _helper("docker")
FAILED = "Nicht alles hat geklappt – bitte die Meldungen oben lesen."


def _narrow(helper_dir, *helpers: RootHelperFile, **kw) -> str:
    return build_setup_script(USER, KEY, sudo=True, helpers=helpers, helper_dir=str(helper_dir), full_sudo=False, **kw)[0]


FULL_RIGHTS_HINT = "„Volle Rechte“ wählen"


def _assert_blocked(done, log, captured, tmp_path, *, full_rights_help: bool = True) -> None:
    assert done.returncode == 1, done.stdout + done.stderr
    assert host_setup.HELPERS_BLOCKED in done.stdout.splitlines()
    assert (FULL_RIGHTS_HINT in done.stdout) is full_rights_help
    assert host_setup.KEY_ADDED not in done.stdout and not (tmp_path / "home" / ".ssh").exists()
    assert not list(captured.iterdir())
    calls = [line for line in log.read_text().splitlines() if line]
    assert all(line.startswith(f"runuser -u {USER} -- test -w ") for line in calls), calls
    assert FAILED not in done.stdout and "Fertig." not in done.stdout
    run = host_setup.evaluate_setup_run(
        done.returncode, f"{host_setup.SETUP_STARTED}\n{done.stdout}", done.stderr, via_sudo=True, sudo_with_password=False,
    )
    assert run.outcome == "helpers_blocked" and host_setup.HELPERS_BLOCKED in run.messages


def test_the_command_has_the_helper_rule_but_no_full_rule():
    script, one_liner = build_setup_script(USER, KEY, sudo=True, helpers=[DOCKER], full_sudo=False)
    assert "docker-root" in script and '"/etc/sudoers.d/nodvard_docker_$U"' in script
    assert "NOPASSWD: ALL" not in script and "nodvard-$U" not in script and "lattice-$U" not in script
    assert "'" not in script and one_liner.count("'") == 2
    for text in (script, one_liner):
        done = subprocess.run(["sh", "-n", "-c", text], capture_output=True, text=True, timeout=20, check=False)
        assert done.returncode == 0, done.stderr


def test_without_helpers_there_is_no_narrow_rule():
    with pytest.raises(SetupError):
        build_setup_script(USER, KEY, sudo=True, full_sudo=False)
    # Für root und ohne sudo ändert der Schalter nichts.
    assert build_setup_script("root", KEY, sudo=True, full_sudo=False) == build_setup_script("root", KEY, sudo=True)
    assert build_setup_script(USER, KEY, full_sudo=False) == build_setup_script(USER, KEY)


@needs_sh
def test_only_the_helper_rule_is_written_and_an_old_full_rule_stays(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    done = _run(_narrow(helper_dir, DOCKER, SHIELD), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(p.name for p in captured.iterdir()) == ["nodvard_docker_deck", "nodvard_shield_deck"]
    assert (captured / "nodvard_docker_deck").read_text() == f"deck ALL=(root) NOPASSWD: {helper_dir}/docker-root\n"
    calls = log.read_text()
    assert "/etc/sudoers.d/nodvard-deck" not in calls and "/etc/sudoers.d/lattice-deck" not in calls, "eine vorhandene volle Regel bleibt"
    assert host_setup.setup_messages(done.stdout) == [
        host_setup.KEY_ADDED,
        "Hilfsskript docker-root (Version 1) eingerichtet, mit eigener sudo-Regel.",
        "Hilfsskript shield-root (Version 1) eingerichtet, mit eigener sudo-Regel.",
    ]
    assert done.stdout.splitlines()[-1].startswith("Fertig.")


@needs_sh
def test_a_damaged_helper_is_an_error(tmp_path):
    bin_dir, _, helper_dir, captured = _helper_sandbox(tmp_path)
    broken = RootHelperFile(**{**DOCKER.__dict__, "sha256": "0" * 64})
    done = _run(_narrow(helper_dir, broken, SHIELD), bin_dir)
    assert done.returncode == 1
    assert (
        "FEHLER: Das Hilfsskript docker-root wurde nicht eingerichtet: Die Datei ließ sich nicht prüfen oder nicht "
        "sicher ablegen." in done.stdout
    )
    assert not (captured / "nodvard_docker_deck").exists() and (captured / "nodvard_shield_deck").exists()
    assert FAILED in done.stdout and "Fertig." not in done.stdout


@pytest.mark.parametrize(
    ("stat_values", "groups", "writable", "reason"),
    [
        ({("%a", "PARENT"): "777"}, USER, (), "darf außer root noch jemand schreiben (Rechte 777"),
        ({("%a", "PARENT"): "1777"}, USER, (), "chmod go-w PARENT"),
        ({("%a", "PARENT"): "775", ("%G", "PARENT"): USER}, USER, (), "(Rechte 775, Gruppe deck)"),
        ({("%a", "PARENT"): "2775", ("%G", "PARENT"): "staff"}, f"{USER} staff", (), "Gruppe staff"),
        ({("%u", "/"): "1000"}, USER, (), "Der Ordner / gehört nicht root."),
        ({("%a", "HELPERS"): "757"}, USER, (), "Im Ordner HELPERS darf"),
        ({}, USER, ("PARENT",), "setfacl -b PARENT"),
    ],
    ids=["ordner-fuer-alle", "ordner-sticky", "gruppe-des-benutzers", "weitere-gruppe", "wurzel", "hilfsordner", "acl"],
)
@needs_sh
def test_an_unsafe_folder_stops_before_anything_changes(tmp_path, stat_values, groups, writable, reason):
    helper_dir = tmp_path / "usr-local-lib" / "nodvard-deck"
    helper_dir.mkdir(parents=True)
    paths = {"PARENT": str(helper_dir.parent), "HELPERS": str(helper_dir), "/": "/"}
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={(fmt, paths[p]): v for (fmt, p), v in stat_values.items()}, groups=groups,
        writable=tuple(paths[p] for p in writable),
    )
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    for name, path in paths.items():
        reason = reason.replace(name, path) if name != "/" else reason
    assert reason in done.stdout
    assert not list(helper_dir.iterdir())


@needs_sh
def test_a_group_the_command_adds_counts_already(tmp_path):
    helper_dir = tmp_path / "usr-local-lib" / "nodvard-deck"
    parent = str(helper_dir.parent)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775", ("%G", parent): "docker"}, groups=USER,
    )
    done = _run(_narrow(helper_dir, DOCKER, groups=["docker"]), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    # Ohne die Gruppe im Befehl ist derselbe Ordner in Ordnung.
    assert _run(_narrow(helper_dir, DOCKER), bin_dir).returncode == 0


def _folder_above(tmp_path: Path) -> str:
    """Legt den Ordner über dem der Hilfsskripte an, wie `_helper_sandbox` ihn nennt."""
    folder = tmp_path / "usr-local-lib"
    folder.mkdir()
    return str(folder)


def _new_user(bin_dir: Path, tmp_path: Path, log: Path, defaults: str = "GROUP=100") -> None:
    """Den Benutzer gibt es erst nach `useradd`, `useradd -D` zeigt `defaults`, getent kennt users, audio und video."""
    created = tmp_path / "created"
    created.unlink(missing_ok=True)
    _stub(bin_dir / "id", f'''case "$1" in
  -gn) echo {USER};;
  -Gn) [ -e "{created}" ] && echo {USER};;
  *) [ -e "{created}" ];;
esac
''')
    _stub(bin_dir / "useradd", f'''[ "$1" != -D ] || {{ printf "%s\\n" {shlex.quote(defaults)}; exit 0; }}
echo "useradd $*" >> "{log}"; touch "{created}"
''')
    _stub(bin_dir / "getent", f'''case "$1 $2" in
  passwd*) echo "{USER}:x:1000:1000::{tmp_path / "home"}:/bin/sh";;
  "group 100"|"group users") echo users:x:100:;;
  "group audio"|"group video") echo "$2:x:29:";;
  *) exit 2;;
esac
''')


@pytest.mark.parametrize(
    ("owner", "defaults"),
    [(USER, "GROUP=100"), ("users", "GROUP=100"), ("video", "GROUP=100\nGROUPS=audio,video")],
    ids=["gruppe-mit-seinem-namen", "standardgruppe", "weitere-gruppe-aus-useradd"],
)
@needs_sh
def test_a_new_user_counts_with_the_groups_useradd_may_give(tmp_path, owner, defaults):
    parent = _folder_above(tmp_path)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775", ("%G", parent): owner}, groups="",
    )
    _new_user(bin_dir, tmp_path, log, defaults)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"(Rechte 775, Gruppe {owner})" in done.stdout


@needs_sh
def test_a_new_user_is_fine_with_a_folder_of_a_foreign_group(tmp_path):
    parent = _folder_above(tmp_path)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "2775", ("%G", parent): "staff"}, groups="",
    )
    _new_user(bin_dir, tmp_path, log)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"Benutzer {USER} angelegt." in done.stdout and (captured / "nodvard_docker_deck").exists()


ACL_DOCKER = "user::rwx\ngroup::r-x\ngroup:docker:rwx\nmask::rwx\nother::r-x\n\n"


@pytest.mark.parametrize(
    ("acl", "mode", "groups"),
    [
        (ACL_DOCKER, "775", []),
        ("user::rwx\ngroup::r-x\ngroup:staff:rwx\nmask::rwx\nother::r-x\n\n", "775", ["docker"]),
        ("user::rwx\ngroup::r-x\ngroup:docker:r-x\nmask::rwx\nother::r-x\n\n", "775", ["docker"]),
        ("user::rwx\nuser:bob:rwx\ngroup::r-x\nmask::rwx\nother::r-x\n\n", "775", []),
        # Die Gruppenbits sind die Maske: Ohne w entscheiden schon sie, die ACL wird gar nicht gelesen.
        ("user::rwx\ngroup::r-x\ngroup:docker:rwx\t#effective:r-x\nmask::r-x\nother::r-x\n\n", "755", ["docker"]),
        ("user::rwx\ngroup::rwx\nother::r-x\ndefault:user::rwx\ndefault:group:docker:rwx\ndefault:mask::rwx\n\n", "775", ["docker"]),
    ],
    ids=[
        "gruppe-kommt-nicht-dazu", "fremde-gruppe", "eigene-gruppe-nur-lesen", "anderer-benutzer", "maske-ohne-w",
        "nur-vorgabe-fuer-neue-dateien",
    ],
)
@needs_sh
def test_an_acl_that_does_not_reach_the_user_is_fine(tmp_path, acl, mode, groups):
    parent = _folder_above(tmp_path)
    bin_dir, _, helper_dir, captured = _helper_sandbox(tmp_path, stat_values={("%a", parent): mode}, acls={parent: acl})
    done = _run(_narrow(helper_dir, DOCKER, groups=groups), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (captured / "nodvard_docker_deck").exists()


@pytest.mark.parametrize(
    ("acl", "groups", "name"),
    [
        (ACL_DOCKER, ["docker"], "docker"),
        ("user::rwx\ngroup::r-x\ngroup:staff:rwx\nmask::rwx\nother::r-x\n\n", [], "staff"),
        ("user::rwx\nuser:deck:rwx\ngroup::r-x\nmask::rwx\nother::r-x\n\n", [], USER),
    ],
    ids=["gruppe-aus-dem-befehl", "gruppe-des-benutzers", "benutzer-selbst"],
)
@needs_sh
def test_an_acl_for_the_user_or_their_groups_stops_it(tmp_path, acl, groups, name):
    parent = _folder_above(tmp_path)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775"}, groups=f"{USER} staff", acls={parent: acl},
    )
    done = _run(_narrow(helper_dir, DOCKER, groups=groups), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert (
        f"FEHLER: Im Ordner {parent} gibt eine ACL (erweiterte Rechte) {name} Schreibrecht, darüber könnte auch der "
        f"Benutzer dort schreiben. Abhilfe als root: setfacl -b {parent} (entfernt die ACL)"
    ) in done.stdout.splitlines()


@needs_sh
def test_an_acl_entry_without_a_name_stops_it_for_a_new_user(tmp_path):
    # useradd kann dem neuen Benutzer oder seiner neuen Gruppe genau diese Nummer geben.
    parent = _folder_above(tmp_path)
    acl = "user::rwx\ngroup::r-x\ngroup:4711:rwx\nmask::rwx\nother::r-x\n\n"
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775"}, groups="", acls={parent: acl},
    )
    _new_user(bin_dir, tmp_path, log)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert "gibt eine ACL (erweiterte Rechte) 4711 Schreibrecht" in done.stdout
    # Für einen vorhandenen Benutzer ist der Eintrag unschädlich.
    (tmp_path / "created").touch()
    assert _run(_narrow(helper_dir, DOCKER), bin_dir).returncode == 0


@needs_sh
def test_a_folder_of_a_group_without_a_name_stops_it_for_a_new_user(tmp_path):
    # Wie beim ACL-Eintrag: Die Nummer kann useradd gleich der neuen Gruppe des Benutzers geben.
    parent = _folder_above(tmp_path)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775", ("%G", parent): "UNKNOWN"}, groups="",
    )
    _new_user(bin_dir, tmp_path, log)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: Im Ordner {parent} darf außer root noch jemand schreiben (Rechte 775, Gruppe UNKNOWN)." in done.stdout
    (tmp_path / "created").touch()
    assert _run(_narrow(helper_dir, DOCKER), bin_dir).returncode == 0


def _without_getfacl(bin_dir: Path) -> None:
    # Auf dem Testrechner kann es ein echtes getfacl geben, eins, das scheitert, steht für ein fehlendes.
    _stub(bin_dir / "getfacl", "exit 127\n")


GETFACL_MISSING = "lässt sich ohne getfacl nicht prüfen, ob eine ACL (erweiterte Rechte) dem Benutzer Schreibrecht gibt."


@needs_sh
def test_an_acl_that_cannot_be_read_stops_it(tmp_path):
    parent = _folder_above(tmp_path)
    # Eine echte ACL im Format des Kernels (Gruppe 100 rwx, Maske rwx), damit das echte ls sein + zeigt.
    entries = ((0x01, 7, -1), (0x04, 5, -1), (0x08, 7, 100), (0x10, 7, -1), (0x20, 5, -1))
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHi", tag, perm, qualifier) for tag, perm, qualifier in entries)
    try:
        os.setxattr(parent, "system.posix_acl_access", acl)
    except (AttributeError, OSError):
        pytest.skip("Das Dateisystem kann hier keine ACL setzen.")
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "775", ("%G", parent): "staff"},
    )
    _without_getfacl(bin_dir)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: Für den Ordner {parent} {GETFACL_MISSING} Abhilfe als root: Paket acl installieren" in done.stdout


@needs_sh
def test_without_getfacl_a_busybox_ls_counts_as_a_possible_acl(tmp_path):
    parent = _folder_above(tmp_path)
    bin_dir, log, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", parent): "2775", ("%G", parent): "staff"},
    )
    _without_getfacl(bin_dir)
    _stub(bin_dir / "ls", '[ "$1" = -ld ] || exit 1\necho "drwxrwsr-x    2 root     staff         4096 Oct 21 12:00 $2"\n')
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert (
        f"{GETFACL_MISSING} Abhilfe als root: Paket acl installieren (apt install acl, auf Alpine apk add acl) oder "
        f"chmod g-w {parent}"
    ) in done.stdout
    # Mit dem ls der coreutils und ohne + ist derselbe Ordner in Ordnung.
    (bin_dir / "ls").unlink()
    assert _run(_narrow(helper_dir, DOCKER), bin_dir).returncode == 0


@needs_sh
def test_the_helper_folder_as_a_link_stops_it_with_the_way_out(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    helper_dir.parent.mkdir(parents=True)
    helper_dir.symlink_to(elsewhere, target_is_directory=True)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: {helper_dir} ist ein Link." in done.stdout and f"rm {helper_dir} (entfernt nur den Link" in done.stdout
    assert not list(elsewhere.iterdir())


@needs_sh
def test_a_link_further_up_is_never_offered_for_removal(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    real = tmp_path / "real-lib"
    real.mkdir()
    helper_dir.parent.symlink_to(real, target_is_directory=True)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: {helper_dir.parent} ist ein Link." in done.stdout and "rm " not in done.stdout
    assert not list(real.iterdir())


def _stat_like_the_real_one(bin_dir) -> None:
    # Wie die Attrappe, aber wie das echte stat ohne Antwort für einen Pfad, den es nicht gibt.
    _stub(bin_dir / "stat", '''[ "$1" = -c ] && [ -e "$3" ] || exit 1
case "$2" in %u) echo 0;; %a) echo 755;; %G) echo root;; *) exit 2;; esac
''')


@needs_sh
def test_a_missing_folder_above_is_named_with_mkdir(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    _stat_like_the_real_one(bin_dir)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: Den Ordner {helper_dir.parent} gibt es nicht. Abhilfe als root: mkdir -p {helper_dir.parent}" in done.stdout
    assert "gehört nicht root" not in done.stdout


@needs_sh
def test_a_file_where_the_helper_folder_belongs_stops_it(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    helper_dir.parent.mkdir(parents=True)
    helper_dir.write_text("kein Ordner")
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    _assert_blocked(done, log, captured, tmp_path)
    assert f"FEHLER: {helper_dir} ist kein Ordner. Abhilfe als root: mv {helper_dir} {helper_dir}.alt" in done.stdout
    assert helper_dir.read_text() == "kein Ordner"


@needs_sh
def test_missing_tools_stop_it_and_full_rights_would_help(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    only = tmp_path / "only"
    only.mkdir()
    for tool in bin_dir.iterdir():
        (only / tool.name).symlink_to(tool)
    for tool in ("cut", "dirname", "sha256sum"):
        (only / tool).symlink_to(shutil.which(tool))
    done = subprocess.run(
        [shutil.which("sh"), "-c", _narrow(helper_dir, DOCKER)], capture_output=True, text=True,
        env={"PATH": str(only), "LC_ALL": "C.UTF-8"}, timeout=30, check=False,
    )
    _assert_blocked(done, log, captured, tmp_path)
    assert "FEHLER: base64, sha256sum oder stat fehlt hier." in done.stdout


def test_the_lines_stay_readable_for_a_long_group_name():
    # Was über `_MAX_MESSAGE_LENGTH` hinausgeht, schneidet „Mit Passwort einrichten“ ab, mitsamt der Abhilfe.
    script = _narrow(host_setup.ROOT_HELPER_DIR, DOCKER)
    group = "g" * 32
    for path in (host_setup.ROOT_HELPER_DIR, str(Path(host_setup.ROOT_HELPER_DIR).parent)):
        for line in script.splitlines():
            for text in line.split('echo "')[1:]:
                text = text.split('"; ')[0].split('"; fi')[0]
                filled = text.replace('$(stat -c %a "$1" 2>/dev/null)', "2775").replace('$(stat -c %G "$1" 2>/dev/null)', group)
                filled = filled.replace("$AN", group).replace("$1", path).replace("$D", path)
                assert len(filled) <= 300, filled


@needs_sh
def test_real_tools_name_the_first_unsafe_folder(tmp_path):
    # Mit echtem stat, id und runuser; der tmp-Ordner liegt immer unter einem, den nicht nur root ändern kann.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "visudo", "exit 0\n")
    helper_dir = tmp_path / "lib" / "nodvard-deck"
    helper_dir.parent.mkdir()
    script = _narrow(helper_dir, DOCKER)
    done = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": f"{bin_dir}:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert host_setup.HELPERS_BLOCKED in done.stdout.splitlines()
    first = done.stdout.splitlines()[0]
    assert first.startswith("FEHLER: ") and "Abhilfe als root:" in first
    assert host_setup.KEY_ADDED not in done.stdout and not helper_dir.exists()


def test_the_check_comes_before_any_change():
    script = build_setup_script(
        USER, KEY, sudo=True, helpers=[DOCKER], full_sudo=False, groups=["docker"], report_state=True,
    )[0]
    check = script.index(host_setup.HELPERS_BLOCKED)
    for change in ("ST before", "Q useradd", "R mkdir", "usermod -aG", host_setup.KEY_ADDED, "install -d", "VP=; visudo"):
        assert check < script.index(change), change
    # Davor liest useradd nur seine Vorgaben.
    assert script[:check].count("useradd") == script[:check].count("useradd -D 2>/dev/null") == 1
    # Mit voller Regel gibt es die Vorprüfung nicht, dort reicht ein Hinweis.
    assert host_setup.HELPERS_BLOCKED not in build_setup_script(USER, KEY, sudo=True, helpers=[DOCKER])[0]


@needs_sh
def test_a_helper_rule_that_breaks_the_config_is_an_error(tmp_path):
    # Die Gesamtprüfung vorher klappt, die nach der Regel für docker-root nicht.
    bin_dir, log, helper_dir, _ = _helper_sandbox(tmp_path, visudo_whole_fails_on_call=2)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    assert done.returncode == 1
    assert "rm -f /etc/sudoers.d/nodvard_docker_deck" in log.read_text()
    assert "FEHLER: Das Hilfsskript docker-root wurde nicht eingerichtet: Seine sudo-Regel" in done.stdout


@needs_sh
def test_without_visudo_nothing_changes(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path, with_visudo=False)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    # Ohne sudo hilft auch die volle Regel nicht.
    _assert_blocked(done, log, captured, tmp_path, full_rights_help=False)
    assert "FEHLER: sudo ist nicht installiert" in done.stdout
    assert not helper_dir.exists()


@needs_sh
def test_a_foreign_visudo_error_does_not_stop_the_helpers(tmp_path):
    bin_dir, _, helper_dir, captured = _helper_sandbox(tmp_path, visudo_whole_fails=True)
    done = _run(_narrow(helper_dir, DOCKER), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Hinweis: visudo meldet schon vor der Einrichtung einen Fehler" in done.stdout
    assert "visudo: /etc/sudoers.d/010_nopasswd: bad permissions" in done.stdout
    assert (captured / "nodvard_docker_deck").exists()


def test_notes_say_there_is_no_full_root():
    notes = host_setup.setup_notes(USER, groups=[], sudo=True, helpers=["docker-root"], full_sudo=False)
    text = " ".join(notes)
    assert "kein volles root" in text and "docker-root" in text and "nicht zurück" in text
    assert "praktisch alles" not in text and "sudo sh -c" not in text
    # Eine Gruppe wie docker bleibt so mächtig, wie sie ist.
    with_group = host_setup.setup_notes(USER, groups=["docker"], sudo=True, helpers=["docker-root"], full_sudo=False)
    assert any("Wer in der Gruppe docker ist, kann auf dem Server praktisch alles." == n for n in with_group)


def test_setup_record_knows_only_the_helper_rules():
    out = "Hilfsskript docker-root (Version 1) eingerichtet, mit eigener sudo-Regel.\n"
    record = host_setup.setup_record(
        USER, host_setup.setup_report(out, user=USER), sudo=True, helpers=[DOCKER], full_sudo=False,
    )
    rule = f"{USER} ALL=(root) NOPASSWD: {host_setup.ROOT_HELPER_DIR}/docker-root\n"
    assert record["sudoers"] == {"/etc/sudoers.d/nodvard_docker_deck": hashlib.sha256(rule.encode()).hexdigest()}
    assert record["legacy_sudoers"] == {}
