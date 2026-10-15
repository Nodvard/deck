"""Einrichtungsbefehl mit Hilfsskripten (`build_setup_script(..., helpers=...)`): Die Skripte der Module kommen base64-
kodiert mit, werden auf dem Server gegen ihre sha256 geprüft und root-eigen abgelegt, jedes mit einer eigenen sudo-Regel
nur für sich. Das alles nur, wenn jeder Ordner bis `/` root gehört und der Benutzer dort nicht schreiben kann. Was
scheitert, gibt einen Hinweis; die übrige Einrichtung (volle Regel, Schlüssel) läuft weiter.

Läuft das Skript wirklich in `sh`, gegen die Attrappen aus `test_host_setup_script.py` plus eigene für `stat`, `id`
und `install`."""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path

import pytest
from test_host_setup_script import KEY, _run, _sandbox, _stub, needs_sh

from nodvard_deck.ext.root_helpers import RootHelperFile
from nodvard_deck.services import host_setup
from nodvard_deck.services.host_setup import SetupError, build_setup_script


def _helper(ext_id: str, version: int = 1, body: str = "") -> RootHelperFile:
    text = f"#!/bin/sh\nset -eu\nHELPER_VERSION={version}\n{body}[ \"${{1:-}}\" = version ] && echo {version}\n"
    return RootHelperFile(
        ext_id=ext_id, ext_name=f"Modul {ext_id}", version=version,
        install_path=f"/usr/local/lib/nodvard-deck/{ext_id}-root", text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


SHIELD, SYSTEM = _helper("shield"), _helper("system", 2)


USER = "deck"


def _helper_sandbox(
    tmp_path: Path, *, stat_values: dict[tuple[str, str], str] | None = None, groups: str = USER,
    writable: tuple[str, ...] = (), **kw
):
    """Wie `_sandbox`, dazu ein Ordner für die Hilfsskripte im tmp-Ordner. `install` kopiert dorthin wirklich und legt
    jede sudoers-Datei unter `captured/` ab; `stat` meldet root und 755, außer für Einträge in `stat_values`
    (`(format, pfad) -> wert`); `id -Gn` nennt `groups`; `runuser ... test -w` sagt nur für `writable` ja (der Test
    läuft womöglich als root, der überall schreiben darf)."""
    bin_dir, _, log = _sandbox(tmp_path, user=USER, **kw)
    can_write = "".join(f"    {shlex.quote(path)}) exit 0;;\n" for path in writable)
    _stub(bin_dir / "runuser", f'''echo "runuser $*" >> "{log}"
shift 2
[ "$1" = "--" ] && shift
if [ "$1" = test ] && [ "$2" = -w ]; then
  case "$3" in
{can_write}    *) exit 1;;
  esac
fi
exec "$@"
''')
    helper_dir = tmp_path / "usr-local-lib" / "nodvard-deck"
    captured = tmp_path / "captured"
    captured.mkdir()
    _stub(bin_dir / "install", f'''echo "install $*" >> "{log}"
if [ "$1" = "-d" ]; then for a in "$@"; do last=$a; done; mkdir -p "$last"; exit 0; fi
for a in "$@"; do src=$dest; dest=$a; done
case "$dest" in
  /etc/sudoers.d/*) cp "$src" "{captured}/${{dest##*/}}";;
  "{helper_dir}"/*) cp "$src" "$dest";;
esac
exit 0
''')
    cases = "".join(f'  {shlex.quote(f"{fmt}|{path}")}) echo {shlex.quote(value)};;\n' for (fmt, path), value in (stat_values or {}).items())
    _stub(bin_dir / "stat", f'''[ "$1" = -c ] || exit 2
case "$2|$3" in
{cases}  %u\\|*) echo 0;;
  %a\\|*) echo 755;;
  %G\\|*) echo root;;
  *) exit 2;;
esac
''')
    _stub(bin_dir / "id", f'''case "$1" in
  -gn) echo {USER};;
  -Gn) echo {groups};;
  *) exit 0;;
esac
''')
    return bin_dir, log, helper_dir, captured


def _script(helper_dir: Path, *helpers: RootHelperFile, user: str = USER, sudo: bool = True) -> str:
    return build_setup_script(user, KEY, sudo=sudo, helpers=helpers, helper_dir=str(helper_dir))[0]


@needs_sh
def test_helpers_are_installed_with_their_own_rule_next_to_the_full_one(tmp_path):
    bin_dir, log, helper_dir, captured = _helper_sandbox(tmp_path)
    done = _run(_script(helper_dir, SYSTEM, SHIELD), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (helper_dir / "shield-root").read_text() == SHIELD.text
    assert (helper_dir / "system-root").read_text() == SYSTEM.text
    assert (captured / "nodvard-deck").read_text() == "deck ALL=(root) NOPASSWD: ALL\n"
    assert (captured / "nodvard_shield_deck").read_text() == f"deck ALL=(root) NOPASSWD: {helper_dir}/shield-root\n"
    assert (captured / "nodvard_system_deck").read_text() == f"deck ALL=(root) NOPASSWD: {helper_dir}/system-root\n"
    calls = log.read_text()
    assert "install -m 0755 -o root -g root " in calls and f" {helper_dir}/shield-root" in calls
    assert f"install -d -m 0755 -o root -g root {helper_dir}" in calls
    lines = done.stdout.splitlines()
    assert "Hilfsskript shield-root (Version 1) eingerichtet, mit eigener sudo-Regel." in lines
    assert "Hilfsskript system-root (Version 2) eingerichtet, mit eigener sudo-Regel." in lines
    assert lines[-1].startswith("Fertig.") and "Hinweis" not in done.stdout
    assert host_setup.setup_messages(done.stdout) == [
        "Hilfsskript shield-root (Version 1) eingerichtet, mit eigener sudo-Regel.",
        "Hilfsskript system-root (Version 2) eingerichtet, mit eigener sudo-Regel.",
    ]

    # Ein zweiter Lauf ändert nichts und meldet dasselbe.
    again = _run(_script(helper_dir, SYSTEM, SHIELD), bin_dir)
    assert again.returncode == 0 and again.stdout == done.stdout


@needs_sh
def test_a_damaged_helper_is_not_installed_and_gets_no_rule(tmp_path):
    bin_dir, _, helper_dir, captured = _helper_sandbox(tmp_path)
    broken = RootHelperFile(**{**SHIELD.__dict__, "sha256": "0" * 64})
    done = _run(_script(helper_dir, broken, SYSTEM), bin_dir)
    assert done.returncode == 0, "ein Hilfsskript ist bisher nicht nötig, der Rest der Einrichtung gilt"
    assert "Hinweis: Das Hilfsskript shield-root wurde nicht eingerichtet." in done.stdout
    assert not (helper_dir / "shield-root").exists() and not (captured / "nodvard_shield_deck").exists()
    assert (helper_dir / "system-root").exists() and (captured / "nodvard_system_deck").exists()
    assert (captured / "nodvard-deck").exists() and "Fertig." in done.stdout


@pytest.mark.parametrize(
    ("stat_values", "groups", "writable"),
    [
        ({("%u", "PARENT"): "1000"}, USER, ()),
        ({("%a", "PARENT"): "777"}, USER, ()),
        ({("%a", "PARENT"): "1777"}, USER, ()),
        ({("%a", "PARENT"): "2775", ("%G", "PARENT"): "staff"}, f"{USER} staff", ()),
        ({("%u", "/"): "1000"}, USER, ()),
        ({("%a", "HELPERS"): "757"}, USER, ()),
        ({("%u", "FILE"): "1000"}, USER, ()),
        ({}, USER, ("PARENT",)),
    ],
    ids=[
        "ordner-gehoert-benutzer", "ordner-fuer-alle", "ordner-sticky", "gruppe-des-benutzers", "wurzel", "hilfsordner",
        "datei", "benutzer-darf-schreiben",
    ],
)
@needs_sh
def test_helpers_are_skipped_unless_only_root_can_change_them(tmp_path, stat_values, groups, writable):
    """`benutzer-darf-schreiben`: Rechte, die man an den Bits nicht sieht (etwa eine ACL), zählen auch."""
    helper_dir = tmp_path / "usr-local-lib" / "nodvard-deck"
    paths = {"PARENT": str(helper_dir.parent), "HELPERS": str(helper_dir), "FILE": str(helper_dir / "shield-root"), "/": "/"}
    bin_dir, _, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={(fmt, paths[p]): v for (fmt, p), v in stat_values.items()}, groups=groups,
        writable=tuple(paths[p] for p in writable),
    )
    done = _run(_script(helper_dir, SHIELD), bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (captured / "nodvard_shield_deck").exists(), "ohne sichere Ablage keine eigene Regel"
    assert "eingerichtet, mit eigener sudo-Regel" not in done.stdout
    assert "Hinweis:" in done.stdout and (captured / "nodvard-deck").exists() and "Fertig." in done.stdout


@needs_sh
def test_a_group_writable_folder_is_fine_when_the_user_is_not_in_that_group(tmp_path):
    helper_dir = tmp_path / "usr-local-lib" / "nodvard-deck"
    bin_dir, _, helper_dir, captured = _helper_sandbox(
        tmp_path, stat_values={("%a", str(helper_dir.parent)): "2775", ("%G", str(helper_dir.parent)): "staff"},
        groups=f"{USER} docker",
    )
    done = _run(_script(helper_dir, SHIELD), bin_dir)
    assert done.returncode == 0 and (captured / "nodvard_shield_deck").exists(), done.stdout


@needs_sh
def test_a_symlinked_helper_folder_is_never_used(tmp_path):
    bin_dir, _, helper_dir, captured = _helper_sandbox(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    helper_dir.parent.mkdir(parents=True)
    helper_dir.symlink_to(elsewhere, target_is_directory=True)
    done = _run(_script(helper_dir, SHIELD), bin_dir)
    assert done.returncode == 0
    assert not list(elsewhere.iterdir()) and not (captured / "nodvard_shield_deck").exists()
    assert "die Hilfsskripte wurden übersprungen" in done.stdout


@needs_sh
def test_without_visudo_the_helpers_are_skipped_quietly(tmp_path):
    bin_dir, _, helper_dir, _ = _helper_sandbox(tmp_path, with_visudo=False)
    done = _run(_script(helper_dir, SHIELD), bin_dir)
    assert done.returncode == 0
    assert "sudo ist nicht installiert" in done.stdout and "Hilfsskript" not in done.stdout
    assert not (helper_dir / "shield-root").exists()


@needs_sh
def test_a_helper_rule_is_taken_back_when_the_whole_config_breaks(tmp_path):
    # Erste Gesamtprüfung (volle Regel) klappt, die zweite (nach der Regel für shield-root) nicht.
    bin_dir, log, helper_dir, _ = _helper_sandbox(tmp_path, visudo_whole_fails_on_call=2)
    done = _run(_script(helper_dir, SHIELD), bin_dir)
    assert done.returncode == 0, done.stdout
    assert "rm -f /etc/sudoers.d/nodvard_shield_deck" in log.read_text()
    assert "Hinweis: Das Hilfsskript shield-root wurde nicht eingerichtet." in done.stdout


@needs_sh
def test_temp_files_are_removed(tmp_path):
    bin_dir, _, helper_dir, _ = _helper_sandbox(tmp_path)
    script = _script(helper_dir, SHIELD, SYSTEM)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8", "TMPDIR": str(tmpdir)}
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, timeout=30, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert list(tmpdir.iterdir()) == []


def test_helpers_only_come_with_sudo_and_never_for_root():
    for user, sudo in ((USER, False), ("root", True), ("root", False)):
        script, _ = build_setup_script(user, KEY, sudo=sudo, helpers=[SHIELD])
        assert "Hilfsskript" not in script and "base64" not in script, (user, sudo)


def test_with_helpers_the_script_keeps_its_shape():
    script, one_liner = build_setup_script(USER, KEY, sudo=True, helpers=[SHIELD, SYSTEM])
    assert "'" not in script and "\n\n" not in script
    assert one_liner.count("'") == 2 and "\n" not in one_liner
    assert script.index("NOPASSWD: ALL") < script.index("HS shield-root") < script.index("HS system-root")
    assert script.index("HS system-root") < script.index('[ -z "$F" ]'), "vor der Schlussprüfung"
    for done in (
        subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True, timeout=20, check=False),
        subprocess.run(["sh", "-n", "-c", one_liner], capture_output=True, text=True, timeout=20, check=False),
    ):
        assert done.returncode == 0, done.stderr
    assert 'D="/usr/local/lib/nodvard-deck"' in script and '"/etc/sudoers.d/nodvard_shield_$U"' in script


@pytest.mark.parametrize(
    "bad",
    [
        RootHelperFile(**{**SHIELD.__dict__, "ext_id": "Shield"}),
        RootHelperFile(**{**SHIELD.__dict__, "ext_id": "a b"}),
        RootHelperFile(**{**SHIELD.__dict__, "ext_id": "x;id"}),
        RootHelperFile(**{**SHIELD.__dict__, "sha256": "zz"}),
        RootHelperFile(**{**SHIELD.__dict__, "version": 0}),
    ],
)
def test_invalid_helpers_are_refused(bad):
    with pytest.raises(SetupError):
        build_setup_script(USER, KEY, sudo=True, helpers=[bad])


def test_a_helper_twice_or_a_strange_folder_is_refused():
    with pytest.raises(SetupError):
        build_setup_script(USER, KEY, sudo=True, helpers=[SHIELD, SHIELD])
    for folder in ("relativ", "/a//b", "/a/../b", '/a"b', "/a b", "/a/$(id)"):
        with pytest.raises(SetupError):
            build_setup_script(USER, KEY, sudo=True, helpers=[SHIELD], helper_dir=folder)


def test_a_command_that_would_be_too_long_is_refused():
    big = [_helper(f"modul-{i}", body="#" * 16000 + "\n") for i in range(6)]
    with pytest.raises(SetupError, match="zu lang"):
        build_setup_script(USER, KEY, sudo=True, helpers=big)


def test_notes_name_the_helpers():
    notes = host_setup.setup_notes(USER, groups=[], sudo=True, helpers=["shield-root", "system-root"])
    assert any("shield-root, system-root" in n and "Die Module nutzen sie schon" in n for n in notes)
    assert not any("nutzt sie noch nicht" in n for n in notes)
    assert not any("Hilfsskripte" in n for n in host_setup.setup_notes(USER, groups=[], sudo=True))
    assert not any("Hilfsskripte" in n for n in host_setup.setup_notes(USER, groups=[], sudo=False, helpers=["x-root"]))
