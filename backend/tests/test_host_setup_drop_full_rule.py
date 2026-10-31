"""Einrichtung mit `drop_full_rule`: Die volle Regel von Nodvard Deck bleibt nur weg, wenn danach jedes Hilfsskript
über seine eigene Regel antwortet. Läuft in `sh` gegen ein Verzeichnis statt /etc/sudoers.d und eine sudo-Attrappe."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from test_host_setup_helpers import SHIELD, USER, _helper, _helper_sandbox
from test_host_setup_script import KEY, _run, _stub, needs_sh

from nodvard_deck.ext.root_helpers import RootHelperFile
from nodvard_deck.services import host_setup
from nodvard_deck.services.host_setup import SetupError, build_setup_script

DOCKER = _helper("docker")
FULL = f"{USER} ALL=(root) NOPASSWD: ALL\n"
FAILED = "Nicht alles hat geklappt – bitte die Meldungen oben lesen."
RESTORED = "FEHLER: Die volle sudo-Regel ist wieder da, weil"


def _sandbox(tmp_path: Path, *, rules: dict[str, str] | None = None, **kw):
    """`sudo -n BEFEHL` läuft nur mit der vollen Regel oder einer Regel genau für BEFEHL. Schalter sind Dateien in
    `tmp_path`: `deaf-<name>`, `ignored-<name>`, `foreign`, `slow` (hängt ohne volle Regel), `rm-fails-<name>`."""
    bin_dir, log, helper_dir, _ = _helper_sandbox(tmp_path, **kw)
    sudoers = tmp_path / "sudoers.d"
    sudoers.mkdir()
    for name, text in (rules or {}).items():
        (sudoers / name).write_text(text)
    _stub(bin_dir / "install", f'''echo "install $*" >> "{log}"
if [ "$1" = "-d" ]; then for a in "$@"; do last=$a; done; mkdir -p "$last"; exit 0; fi
for a in "$@"; do src=$dest; dest=$a; done
case "$dest" in "{sudoers}"/*|"{helper_dir}"/*) cp "$src" "$dest";; esac
exit 0
''')
    _stub(bin_dir / "sudo", f'''echo "sudo $*" >> "{log}"
[ "$1" = -n ] || exit 1
shift
full=0; ! grep -qsx "{USER} ALL=(root) NOPASSWD: ALL" "{sudoers}"/* || full=1
if [ "$1" = -l ]; then
  [ ! -e "{tmp_path}/foreign" ] || echo "    (ALL : ALL) NOPASSWD: ALL"
  [ "$full" = 0 ] || echo "    (root) NOPASSWD: ALL"
  exit 0
fi
name=$(basename "$1")
[ ! -e "{tmp_path}/deaf-$name" ] || exit 1
if [ "$full" = 0 ] && [ -e "{tmp_path}/slow" ]; then touch "{tmp_path}/waiting"; sleep 30; fi
if [ "$full" = 0 ] && [ ! -e "{tmp_path}/foreign" ]; then
  [ ! -e "{tmp_path}/ignored-$name" ] || exit 1
  grep -qsx "{USER} ALL=(root) NOPASSWD: $1" "{sudoers}"/* || exit 1
fi
exec sh "$@"
''')
    _stub(bin_dir / "rm", f'''echo "rm $*" >> "{log}"
for a in "$@"; do last=$a; done
[ ! -e "{tmp_path}/rm-fails-${{last##*/}}" ] || exit 1
exec /bin/rm "$@"
''')
    return bin_dir, log, helper_dir, sudoers


def _drop(tmp_path: Path, helper_dir: Path, sudoers: Path, *helpers: RootHelperFile, bin_dir: Path):
    script = build_setup_script(
        USER, KEY, sudo=True, helpers=helpers, helper_dir=str(helper_dir), full_sudo=False, drop_full_rule=True,
    )[0].replace("/etc/sudoers.d/", f"{sudoers}/")
    return _run(script, bin_dir)


def _names(sudoers: Path) -> list[str]:
    return sorted(p.name for p in sudoers.iterdir())


@needs_sh
def test_the_full_rule_goes_once_every_helper_answers_without_it(tmp_path):
    bin_dir, log, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, SHIELD, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _names(sudoers) == ["nodvard_docker_deck", "nodvard_shield_deck"]
    assert host_setup.setup_messages(done.stdout)[-1] == "Die volle sudo-Regel nodvard-deck wurde entfernt."
    assert "Hinweis" not in done.stdout and done.stdout.splitlines()[-1].startswith("Fertig.")
    calls = log.read_text().splitlines()
    removal = next(i for i, c in enumerate(calls) if c == f"rm -f {sudoers}/nodvard-deck")
    probes = [i for i, c in enumerate(calls) if c.startswith("sudo -n ") and c.endswith(" version")]
    assert min(probes) < removal < max(probes), "erst prüfen, dann entfernen, dann noch einmal prüfen"
    assert not list(Path(tmp_path).glob("tmp*")), "keine Reste"


@needs_sh
def test_the_rule_under_the_old_name_goes_too(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL, "lattice-deck": FULL})
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _names(sudoers) == ["nodvard_docker_deck"]
    report = host_setup.setup_report(done.stdout, user=USER)
    assert report["actions"] == ["full_rule_removed", "key_added"]
    assert report["removed_rules"] == ["lattice-deck", "nodvard-deck"]


@needs_sh
@pytest.mark.parametrize("text", ["deck ALL=(ALL) ALL\n", FULL + "# von Hand\n", FULL.rstrip("\n"), ""])
def test_a_rule_with_other_content_stays(tmp_path, text):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": text})
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (sudoers / "nodvard-deck").read_text() == text
    assert "nodvard-deck ist eine andere Regel als die von Nodvard Deck und bleibt stehen" in done.stdout
    assert "wurde entfernt" not in done.stdout and "nichts zu entfernen" not in done.stdout


@needs_sh
def test_a_link_stays(tmp_path):
    target = tmp_path / "ziel"
    target.write_text(FULL)
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path)
    (sudoers / "nodvard-deck").symlink_to(target)
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (sudoers / "nodvard-deck").is_symlink() and target.read_text() == FULL
    assert "nodvard-deck ist ein Link und bleibt stehen" in done.stdout and "wurde entfernt" not in done.stdout


@needs_sh
def test_a_helper_that_does_not_answer_keeps_the_rule(tmp_path):
    bin_dir, log, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    (tmp_path / "deaf-shield-root").write_text("")
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, SHIELD, bin_dir=bin_dir)
    assert done.returncode == 1
    assert (sudoers / "nodvard-deck").read_text() == FULL
    assert (
        "FEHLER: Die volle sudo-Regel bleibt stehen, weil diese Hilfsskripte über sudo nicht antworten: shield-root."
        in done.stdout
    )
    assert FAILED in done.stdout and f"rm -f {sudoers}/" not in log.read_text()


@needs_sh
@pytest.mark.parametrize("ignored", ["docker-root", "shield-root"])
def test_the_rule_comes_back_when_a_helper_needs_it(tmp_path, ignored):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL, "lattice-deck": FULL})
    (tmp_path / f"ignored-{ignored}").write_text("")
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, SHIELD, bin_dir=bin_dir)
    assert done.returncode == 1
    assert (sudoers / "nodvard-deck").read_text() == FULL and (sudoers / "lattice-deck").read_text() == FULL
    assert f"{RESTORED} ohne sie diese Hilfsskripte über sudo nicht antworten: {ignored}." in done.stdout
    assert "wurde entfernt" not in done.stdout and FAILED in done.stdout
    assert host_setup.setup_report(done.stdout, user=USER)["removed_rules"] == []


@needs_sh
def test_the_rule_comes_back_when_the_config_breaks(tmp_path):
    # Gesamtprüfung: vorher, nach der Regel für docker-root, nach dem Entfernen.
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL}, visudo_whole_fails_on_call=3)
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 1
    assert (sudoers / "nodvard-deck").read_text() == FULL
    assert f"{RESTORED} visudo danach einen Fehler meldet." in done.stdout


@needs_sh
def test_an_abort_before_the_check_brings_the_rule_back(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    (tmp_path / "slow").write_text("")
    script = build_setup_script(
        USER, KEY, sudo=True, helpers=[DOCKER], helper_dir=str(helper_dir), full_sudo=False, drop_full_rule=True,
    )[0].replace("/etc/sudoers.d/", f"{sudoers}/")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    run = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, text=True, start_new_session=True)
    deadline = time.monotonic() + 20
    while not (tmp_path / "waiting").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not (sudoers / "nodvard-deck").exists(), "die Regel ist gelöscht, die Prüfung läuft"
    # Wie eine abgerissene Verbindung: das Signal geht an die ganze Gruppe.
    os.killpg(run.pid, signal.SIGHUP)
    out, _ = run.communicate(timeout=20)
    assert run.returncode != 0
    assert (sudoers / "nodvard-deck").read_text() == FULL
    assert "wurde entfernt" not in out


@needs_sh
def test_a_rule_that_cannot_be_deleted_leaves_everything_as_it_was(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL, "lattice-deck": FULL})
    (tmp_path / "rm-fails-nodvard-deck").write_text("")
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 1
    assert (sudoers / "nodvard-deck").read_text() == FULL and (sudoers / "lattice-deck").read_text() == FULL
    assert f"{RESTORED} sich nodvard-deck nicht löschen ließ." in done.stdout and FAILED in done.stdout


@needs_sh
def test_without_a_rule_to_remove_nothing_is_probed(tmp_path):
    bin_dir, log, helper_dir, sudoers = _sandbox(tmp_path)
    (tmp_path / "deaf-docker-root").write_text("")
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "nichts zu entfernen" in done.stdout and "FEHLER" not in done.stdout
    assert " version" not in log.read_text()


@needs_sh
def test_without_cmp_the_rule_stays(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    script = build_setup_script(
        USER, KEY, sudo=True, helpers=[DOCKER], helper_dir=str(helper_dir), full_sudo=False, drop_full_rule=True,
    )[0].replace("/etc/sudoers.d/", f"{sudoers}/").replace("command -v cmp", "command -v cmp-fehlt")
    done = _run(script, bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (sudoers / "nodvard-deck").read_text() == FULL
    assert "Hinweis: cmp fehlt hier (Paket diffutils)" in done.stdout


@needs_sh
def test_a_second_run_has_nothing_to_do(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    assert _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir).returncode == 0
    again = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "Hinweis: Eine volle sudo-Regel von Nodvard Deck gibt es hier nicht, es war nichts zu entfernen." in (
        again.stdout
    )
    assert _names(sudoers) == ["nodvard_docker_deck"]


@needs_sh
def test_a_foreign_full_rule_is_named_but_stays(tmp_path):
    bin_dir, _, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    (tmp_path / "foreign").write_text("")
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, bin_dir=bin_dir)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Die volle sudo-Regel nodvard-deck wurde entfernt." in done.stdout
    foreign = "Hinweis: deck darf weiter alles per sudo ohne Passwort, über eine Regel, die nicht von Nodvard Deck"
    assert foreign in done.stdout


@needs_sh
def test_a_failed_helper_rule_stops_before_the_removal(tmp_path):
    bin_dir, log, helper_dir, sudoers = _sandbox(tmp_path, rules={"nodvard-deck": FULL})
    broken = RootHelperFile(**{**SHIELD.__dict__, "sha256": "0" * 64})
    done = _drop(tmp_path, helper_dir, sudoers, DOCKER, broken, bin_dir=bin_dir)
    assert done.returncode == 1
    assert (sudoers / "nodvard-deck").read_text() == FULL
    assert "sudo -n" not in log.read_text(), "nach einem Fehler wird nichts mehr angefasst"


def test_only_together_with_the_helper_rules():
    with pytest.raises(SetupError):
        build_setup_script(USER, KEY, sudo=True, helpers=[DOCKER], drop_full_rule=True)
    with pytest.raises(SetupError):
        build_setup_script(USER, KEY, sudo=False, helpers=[DOCKER], full_sudo=False, drop_full_rule=True)
    with pytest.raises(SetupError):
        build_setup_script("root", KEY, sudo=True, helpers=[DOCKER], full_sudo=False, drop_full_rule=True)
    script = build_setup_script(USER, KEY, sudo=True, helpers=[DOCKER], full_sudo=False, drop_full_rule=True)[0]
    assert "'" not in script and "NOPASSWD: ALL" in script
    narrow = build_setup_script(USER, KEY, sudo=True, helpers=[DOCKER], full_sudo=False)[0]
    assert "XA()" not in narrow and '"nodvard-$U"' not in narrow


def test_the_report_counts_only_the_own_rules():
    out = (
        "Die volle sudo-Regel nodvard-deck wurde entfernt.\n"
        "Die volle sudo-Regel lattice-deck wurde entfernt.\n"
        "Die volle sudo-Regel nodvard-anna wurde entfernt.\n"
        "Die volle sudo-Regel 010_pi-nopasswd wurde entfernt.\n"
    )
    report = host_setup.setup_report(out, user=USER)
    assert report["removed_rules"] == ["lattice-deck", "nodvard-deck"]
    rule = hashlib.sha256(FULL.encode()).hexdigest()
    record = host_setup.setup_record(USER, report, sudo=True, helpers=[DOCKER], full_sudo=False, drop_full_rule=True)
    assert record["removed_sudoers"] == {"/etc/sudoers.d/nodvard-deck": rule, "/etc/sudoers.d/lattice-deck": rule}
    assert record["legacy_sudoers"] == {} and "/etc/sudoers.d/nodvard-deck" not in record["sudoers"]
    # Ohne den Schalter zählt die Zeile nicht, gleich was der Server ausgibt.
    assert host_setup.setup_record(USER, report, sudo=True, helpers=[DOCKER], full_sudo=False)["removed_sudoers"] == {}


def test_notes_name_the_removal():
    kept = " ".join(host_setup.setup_notes(USER, groups=[], sudo=True, helpers=["docker-root"], full_sudo=False))
    assert "nimmt der Befehl nicht zurück" in kept and "„Volle sudo-Regel entfernen“" in kept
    dropped = " ".join(host_setup.setup_notes(
        USER, groups=[], sudo=True, helpers=["docker-root"], full_sudo=False, drop_full_rule=True,
    ))
    assert "stellt er sie sofort wieder her" in dropped and "nicht zurück" not in dropped
