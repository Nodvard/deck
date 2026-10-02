"""deploy/entrypoint.sh: Besitzrechte am Datenordner und Wechsel vom Benutzer root zu `lattice`.

Startet der Container als root (so liefert ihn das Image aus), setzt das Skript die Besitzrechte an
/app/data auf den Benutzer `lattice` und startet die Anwendung erst danach als dieser Benutzer.
Laeuft der Container schon als Nicht-root (`user:` in Compose, Kubernetes), aendert sich nichts.

Die Skripte laufen echt per Bash/sh; `id`, `chown`, `setpriv` und `python` sind Attrappen im PATH, die
ihre Aufrufe mitschreiben (kein Docker, keine Rechte noetig). `/app` wird im Testexemplar des Skripts
durch einen Ordner unter tmp ersetzt; dass im echten Skript genau `/app/data` fest steht, prueft ein
eigener Test. Ein letzter Test nimmt die echten Programme, wenn der Test als root laeuft.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
DOCKERFILE = ROOT / "deploy" / "Dockerfile"

needs_sh = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None,
    reason="braucht eine echte POSIX-Shell (Attrappen sind Shell-Skripte im PATH)",
)

# `id`: -u -> aktueller Benutzer (STUB_UID); -u/-g lattice -> Nummer des Image-Benutzers.
ID_STUB = """#!/bin/sh
case "$*" in
  "-u") echo "$STUB_UID" ;;
  "-u lattice") [ -n "${STUB_NO_IMAGE_USER:-}" ] && exit 1; echo "${STUB_IMG_UID:-1000}" ;;
  "-g lattice") [ -n "${STUB_NO_IMAGE_USER:-}" ] && exit 1; echo "${STUB_IMG_GID:-1000}" ;;
  *) exit 2 ;;
esac
"""

# `chown`: schreibt die Argumente auf, aendert nichts.
CHOWN_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG.chown"
[ -n "${STUB_CHOWN_FAILS:-}" ] && { echo "chown: Operation not permitted" >&2; exit 1; }
exit 0
"""

# `setpriv`: schreibt die Argumente auf und fuehrt den Rest als der neue Benutzer aus
# (STUB_UID aendert sich auf den Wert von --reuid).
SETPRIV_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG.setpriv"
[ -n "${STUB_SETPRIV_FAILS:-}" ] && { echo "setpriv: setresuid failed" >&2; exit 1; }
uid="$STUB_UID"
while [ $# -gt 0 ]; do
  case "$1" in
    --reuid=*) uid="${1#--reuid=}"; shift ;;
    --*) shift ;;
    *) break ;;
  esac
done
STUB_UID="$uid" exec "$@"
"""

# `python -m nodvard_deck.boot` (spielt eine vorgemerkte Wiederherstellung ein, dann Migration) und `python -m nodvard_deck.rescue`
# (die Notseite): schreibt Benutzer und Aufruf auf.
#   STUB_BOOT_FAILS=1   boot scheitert (Rueckgabe 3), STUB_BOOT_RC=<n> eine andere Rueckgabe (75 = Datenordner gesperrt)
#   STUB_RESCUE_RC=<n>  die Notseite endet sofort mit n (Standard 75 = "neu starten")
#   STUB_RESCUE_WAIT=1  die Notseite laeuft, bis sie SIGTERM bekommt (wie der echte Server), und endet dann mit 0
PYTHON_STUB = """#!/bin/sh
case "$2" in
  nodvard_deck.rescue)
    echo "rescue uid=$STUB_UID boot_exit=${NODVARD_DECK_BOOT_EXIT:-} $*" >> "$STUB_LOG.steps"
    if [ -n "${STUB_RESCUE_WAIT:-}" ]; then
      trap 'echo "rescue-sigterm" >> "$STUB_LOG.steps"; exit 0' TERM
      echo ready >> "$STUB_LOG.ready"
      while :; do sleep 0.1; done
    fi
    exit "${STUB_RESCUE_RC:-75}"
    ;;
esac
echo "boot uid=$STUB_UID $*" >> "$STUB_LOG.steps"
[ -n "${STUB_BOOT_FAILS:-}" ] && { echo "boot: Migration fehlgeschlagen" >&2; exit 3; }
[ -n "${STUB_BOOT_RC:-}" ] && exit "$STUB_BOOT_RC"
exit 0
"""

# `sleep`: der Notausgang `exec sleep 2147483647` darf im Test nicht ewig laufen.
SLEEP_STUB = """#!/bin/sh
case "$1" in
  2147483647) echo "sleep $*" >> "$STUB_LOG.steps"; exit 0 ;;
esac
exec /bin/sleep "$@"
"""

# Die Anwendung: schreibt auf, unter welchem Benutzer sie laeuft.
APP_STUB = """#!/bin/sh
echo "app uid=$STUB_UID $*" >> "$STUB_LOG.steps"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.app = tmp_path / "app"
        self.data = self.app / "data"
        self.data.mkdir(parents=True)
        (self.data / "lattice.db").write_text("db", encoding="utf-8")
        (self.data / "unterordner").mkdir()
        (self.data / "unterordner" / "datei.txt").write_text("x", encoding="utf-8")
        # Ein Link aus dem Datenordner heraus: darf nicht zum Ziel werden.
        self.outside = tmp_path / "ausserhalb"
        self.outside.mkdir()
        (self.outside / "wichtig.txt").write_text("nicht anfassen", encoding="utf-8")
        (self.data / "link-nach-draussen").symlink_to(self.outside)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _executable(self.bin / "id", ID_STUB)
        _executable(self.bin / "chown", CHOWN_STUB)
        _executable(self.bin / "setpriv", SETPRIV_STUB)
        _executable(self.bin / "python", PYTHON_STUB)
        _executable(self.bin / "app", APP_STUB)
        _executable(self.bin / "sleep", SLEEP_STUB)
        self.log = tmp_path / "log"
        text = ENTRYPOINT.read_text(encoding="utf-8")
        assert "/app/data" in text and "cd /app" in text
        self.script = tmp_path / "entrypoint.sh"
        _executable(self.script, text.replace("/app/data", str(self.data)).replace("cd /app", f"cd {self.app}"))

    def run(self, uid: int | str, **env: str) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(self.log), "STUB_UID": str(uid),
            # Nummer des Image-Benutzers: so, dass `find` im Datenordner wirklich etwas zu aendern findet.
            "STUB_IMG_UID": str(os.getuid() + 1), "STUB_IMG_GID": str(os.getgid() + 1),
        }
        full.update(env)
        return subprocess.run(
            ["sh", str(self.script), "app", "--port", "8080"], env=full, capture_output=True, text=True, timeout=60, check=False,
        )

    def lines(self, suffix: str) -> list[str]:
        path = Path(f"{self.log}.{suffix}")
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_entrypoint_is_valid_shell_syntax():
    if shutil.which("sh") is None:
        pytest.skip("keine Shell")
    assert subprocess.run(["sh", "-n", str(ENTRYPOINT)], capture_output=True, check=False).returncode == 0
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", str(ENTRYPOINT)], capture_output=True, check=False).returncode == 0


@needs_sh
def test_as_root_takes_over_the_data_folder_and_then_runs_everything_as_the_image_user(setup):
    result = setup.run(uid=0)
    assert result.returncode == 0, result.stderr
    uid, gid = os.getuid() + 1, os.getgid() + 1
    chown_calls = setup.lines("chown")
    assert chown_calls, "chown wurde aufgerufen"
    for call in chown_calls:
        assert call.startswith(f"-h {uid}:{gid} "), "ohne Links zu folgen (-h) und nur mit Besitzer:Gruppe des Image-Benutzers"
    touched = {arg for call in chown_calls for arg in call.split()[2:]}
    assert str(setup.data / "lattice.db") in touched and str(setup.data / "unterordner" / "datei.txt") in touched
    assert all(path == str(setup.data) or path.startswith(str(setup.data) + os.sep) for path in touched), "nichts ausserhalb des Datenordners"
    assert str(setup.outside / "wichtig.txt") not in touched, "ein Link nach draussen wird nicht verfolgt"
    # Der Wechsel passiert vor allem anderen: boot (Wiederherstellung + Migration) UND Anwendung laufen schon als lattice.
    assert setup.lines("steps") == [f"boot uid={uid} -m nodvard_deck.boot", f"app uid={uid} --port 8080"]
    setpriv_calls = setup.lines("setpriv")
    assert any(f"--reuid={uid}" in c and f"--regid={gid}" in c and "--clear-groups" in c and "--no-new-privs" in c for c in setpriv_calls)


@needs_sh
def test_chown_happens_before_the_switch_and_the_switch_before_the_app(setup, tmp_path):
    # Reihenfolge ueber gemeinsame Zeitmarken: jede Attrappe haengt an dieselbe Datei an.
    order = tmp_path / "order"
    for name in ("chown", "setpriv", "python", "app"):
        stub = setup.bin / name
        stub.write_text(stub.read_text(encoding="utf-8").replace("#!/bin/sh\n", f'#!/bin/sh\necho {name} >> "{order}"\n', 1), encoding="utf-8")
    assert setup.run(uid=0).returncode == 0
    seen = order.read_text(encoding="utf-8").split()
    assert seen.index("chown") < seen.index("setpriv")
    # `setpriv` kommt zweimal vor (Probelauf mit `true`, dann der echte Wechsel); erst danach boot/Anwendung.
    assert max(i for i, n in enumerate(seen) if n == "setpriv") < seen.index("python") < seen.index("app")


@needs_sh
def test_as_non_root_nothing_changes(setup):
    result = setup.run(uid=1000)
    assert result.returncode == 0, result.stderr
    assert setup.lines("chown") == [] and setup.lines("setpriv") == []
    assert setup.lines("steps") == ["boot uid=1000 -m nodvard_deck.boot", "app uid=1000 --port 8080"]


@needs_sh
def test_already_owned_files_are_not_touched_again(setup):
    if os.getuid() == 0:
        for path in [setup.data, *setup.data.rglob("*")]:
            os.lchown(path, 4242, 4242)
        env = {"STUB_IMG_UID": "4242", "STUB_IMG_GID": "4242"}
    else:
        env = {"STUB_IMG_UID": str(os.getuid()), "STUB_IMG_GID": str(os.getgid())}
    assert setup.run(uid=0, **env).returncode == 0
    assert setup.lines("chown") == []
    assert [line.split()[0] for line in setup.lines("steps")] == ["boot", "app"]


@needs_sh
def test_image_user_numbers_fall_back_to_1000_and_never_to_root(setup):
    result = setup.run(uid=0, STUB_NO_IMAGE_USER="1")
    assert result.returncode == 0, result.stderr
    assert setup.lines("chown") and all(c.startswith("-h 1000:1000 ") for c in setup.lines("chown"))
    assert setup.lines("steps")[-1] == "app uid=1000 --port 8080"
    root_result = setup.run(uid=0, STUB_IMG_UID="0")
    assert root_result.returncode != 0 and "app uid=0" not in "".join(setup.lines("steps"))


@needs_sh
def test_failing_chown_only_warns_and_the_app_still_starts(setup):
    result = setup.run(uid=0, STUB_CHOWN_FAILS="1")
    assert result.returncode == 0, result.stderr
    assert "Warnung" in result.stderr
    assert setup.lines("steps")[-1].startswith("app uid=")


@needs_sh
def test_a_failing_boot_step_starts_the_rescue_page_and_the_app_never_runs(setup):
    # Frueher: `set -e` brach den Start ab -- und Docker startete den Container endlos neu. Jetzt kommt die Notseite
    # (als Benutzer lattice, mit dem Port der Anwendung); "neu versuchen" auf der Seite beendet sie mit 75.
    for uid in (0, 1000):
        setup.log.with_name("log.steps").unlink(missing_ok=True)
        result = setup.run(uid=uid, STUB_BOOT_FAILS="1")
        assert result.returncode == 75, result.stderr
        steps = setup.lines("steps")
        assert len(steps) == 2 and steps[0].startswith("boot uid=") and steps[1].startswith("rescue uid="), steps
        assert "app uid" not in "".join(steps)
        assert steps[1].endswith("-m nodvard_deck.rescue --host 127.0.0.1 --port 8080"), "ohne --host lauscht uvicorn nur lokal, die Notseite auch"
        assert "boot_exit=3" in steps[1], "die Notseite weiss, mit welchem Code boot geendet ist"
        assert "Migration fehlgeschlagen" in result.stderr and "Notseite" in result.stderr


@needs_sh
def test_the_rescue_page_runs_as_the_image_user_not_as_root(setup):
    result = setup.run(uid=0, STUB_BOOT_FAILS="1")
    uid = os.getuid() + 1
    assert result.returncode == 75
    assert setup.lines("steps")[1].startswith(f"rescue uid={uid} ")


@needs_sh
def test_the_rescue_page_takes_host_and_port_from_the_start_command(setup):
    cases = {
        ("uvicorn", "nodvard_deck.main:app", "--host", "0.0.0.0", "--port", "9090"): "--host 0.0.0.0 --port 9090",
        ("uvicorn", "--port=9091", "--host=127.0.0.1"): "--host 127.0.0.1 --port 9091",
        ("uvicorn", "--host", "::", "--port", "8080"): "--host :: --port 8080",
        # Fund: Ohne erkennbares --host (oder mit einem Hostnamen) lauschte die Notseite auf ALLEN Schnittstellen, auch wenn
        # die Anwendung absichtlich nur lokal lief. Jetzt: die engste Freigabe, wie uvicorn selbst.
        ("uvicorn", "nodvard_deck.main:app"): "--host 127.0.0.1 --port 8080",
        ("uvicorn", "--host", "localhost", "--port", "8080"): "--host 127.0.0.1 --port 8080",
        ("uvicorn", "--host", "mein-server.lan"): "--host 127.0.0.1 --port 8080",
        ("uvicorn", "--port", "80;touch pwned"): "--host 127.0.0.1 --port 8080",
        ("uvicorn", "--host", "$(touch pwned)", "--port", "abc"): "--host 127.0.0.1 --port 8080",
        ("uvicorn", "--port"): "--host 127.0.0.1 --port 8080",
    }
    for args, expected in cases.items():
        setup.log.with_name("log.steps").unlink(missing_ok=True)
        full = {"PATH": f"{setup.bin}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(setup.log), "STUB_UID": "1000", "STUB_BOOT_FAILS": "1"}
        result = subprocess.run(["sh", str(setup.script), "app", *args], env=full, capture_output=True, text=True, timeout=60, check=False, cwd=setup.tmp)
        assert result.returncode == 75, (args, result.stderr)
        assert setup.lines("steps")[1].endswith(f"-m nodvard_deck.rescue {expected}"), (args, setup.lines("steps"))
        assert not (setup.tmp / "pwned").exists() and not (setup.app / "pwned").exists(), "nichts aus den Argumenten wird ausgefuehrt"


@needs_sh
def test_the_rescue_page_follows_uvicorn_host_from_the_environment(setup):
    # uvicorn liest auch UVICORN_HOST; ein --host im Startbefehl geht vor.
    for args, extra, expected in (
        (("uvicorn", "nodvard_deck.main:app"), {"UVICORN_HOST": "0.0.0.0"}, "--host 0.0.0.0 --port 8080"),
        (("uvicorn", "--host", "127.0.0.1"), {"UVICORN_HOST": "0.0.0.0"}, "--host 127.0.0.1 --port 8080"),
        (("uvicorn",), {"UVICORN_HOST": "$(touch pwned)"}, "--host 127.0.0.1 --port 8080"),
    ):
        setup.log.with_name("log.steps").unlink(missing_ok=True)
        full = {"PATH": f"{setup.bin}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(setup.log), "STUB_UID": "1000", "STUB_BOOT_FAILS": "1", **extra}
        result = subprocess.run(["sh", str(setup.script), "app", *args], env=full, capture_output=True, text=True, timeout=60, check=False, cwd=setup.tmp)
        assert result.returncode == 75, (args, result.stderr)
        assert setup.lines("steps")[1].endswith(f"-m nodvard_deck.rescue {expected}"), (args, extra, setup.lines("steps"))
        assert not (setup.tmp / "pwned").exists()


@needs_sh
def test_when_the_rescue_page_cannot_start_the_container_just_waits_never_a_restart_loop(setup):
    for rc in ("1", "2", "127", "137"):
        setup.log.with_name("log.steps").unlink(missing_ok=True)
        result = setup.run(uid=1000, STUB_BOOT_FAILS="1", STUB_RESCUE_RC=rc)
        steps = setup.lines("steps")
        assert [s.split()[0] for s in steps] == ["boot", "rescue", "sleep"], (rc, steps)
        assert steps[2] == "sleep 2147483647"
        assert result.returncode == 0, "der Prozess endet NICHT mit einem Fehler (sonst startet Docker ihn endlos neu)"
        assert "Notseite nicht startbar" in result.stderr and "app uid" not in "".join(steps)
        assert sum(1 for s in steps if s.startswith("boot")) == 1, "boot lief genau einmal"


@needs_sh
def test_a_stopped_rescue_page_ends_the_container_without_error(setup):
    result = setup.run(uid=1000, STUB_BOOT_FAILS="1", STUB_RESCUE_RC="0")
    assert result.returncode == 0 and [s.split()[0] for s in setup.lines("steps")] == ["boot", "rescue"]


@needs_sh
def test_a_locked_data_folder_is_no_reason_for_the_rescue_page(setup):
    # boot meldet 75: ein anderer Prozess (die laufende Anwendung) benutzt den Datenordner, und es wurde nichts veraendert.
    result = setup.run(uid=1000, STUB_BOOT_RC="75")
    assert result.returncode == 75
    assert [s.split()[0] for s in setup.lines("steps")] == ["boot"]
    assert "Datenordner" in result.stderr


@needs_sh
def test_sigterm_reaches_the_running_rescue_page_and_ends_the_script(setup):
    # `docker stop` schickt SIGTERM an den Entrypoint (PID 1); er muss es an die Notseite weitergeben.
    import signal as _signal
    import time

    full = {"PATH": f"{setup.bin}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(setup.log), "STUB_UID": "1000",
            "STUB_BOOT_FAILS": "1", "STUB_RESCUE_WAIT": "1"}
    proc = subprocess.Popen(["sh", str(setup.script), "app", "--port", "8080"], env=full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = Path(f"{setup.log}.ready")
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.1)
        assert ready.exists(), "die Notseite lief nicht an"
        proc.send_signal(_signal.SIGTERM)
        assert proc.wait(15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    steps = setup.lines("steps")
    assert "rescue-sigterm" in steps, "die Notseite hat das Signal bekommen und sich ordentlich beendet"
    assert not any(s.startswith("sleep") for s in steps) and not any(s.startswith("app") for s in steps)


@needs_sh
def test_boot_gets_no_arguments_from_the_container_command(setup):
    # Die Argumente des Containers (`uvicorn ...`) gehen an `exec "$@"`, nie an boot.
    assert setup.run(uid=1000).returncode == 0
    assert setup.lines("steps")[0] == "boot uid=1000 -m nodvard_deck.boot"


@needs_sh
def test_if_the_user_switch_is_impossible_nothing_runs_as_root(setup):
    result = setup.run(uid=0, STUB_SETPRIV_FAILS="1")
    assert result.returncode != 0
    assert setup.lines("steps") == [], "weder boot noch Anwendung als root"
    assert "user:" in result.stderr, "die Meldung nennt den Ausweg (Compose-Einstellung user)"


@needs_sh
def test_a_second_pass_as_root_after_the_switch_is_refused(setup):
    # Sicherung gegen eine Schleife bzw. gegen "als root weitermachen", falls der Wechsel still nichts taete.
    result = setup.run(uid=0, NODVARD_DECK_PRIV_DROPPED="1")
    assert result.returncode != 0 and setup.lines("steps") == []


def test_script_text_keeps_the_safety_properties():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "/app/data" in text
    assert "-xdev" in text, "bleibt auf dem Dateisystem des Datenordners"
    assert "chown -h" in text, "folgt keinen Links"
    assert "chown -R" not in text and "chmod -R" not in text
    # Rechte im Datenordner setzt erst der Prozess NACH dem Wechsel zu lattice (kein root-chmod, dem ein untergeschobener Link zum Ziel wird).
    before_switch = text.split("exec setpriv", 1)[0]
    assert not any("chmod" in line and not line.lstrip().startswith("#") for line in before_switch.splitlines())
    assert any(line.lstrip().startswith("chmod 700 /app/data") for line in text.splitlines())
    assert "\numask 077\n" in text and text.index("\numask 077\n") < text.index('if [ "$(id -u)" = "0" ]')
    assert text.rindex("setpriv") < text.rindex("python -m nodvard_deck.boot") < text.rindex("python -m nodvard_deck.rescue") < text.rindex('exec "$@"')
    # Der Einstiegspunkt ist `nodvard_deck.boot`, nicht mehr `nodvard_deck.migrate` (die Migration ruft boot selbst auf).
    commands = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert any(line.strip().startswith("python -m nodvard_deck.boot") for line in commands) and not any("nodvard_deck.migrate" in line for line in commands)
    # Nie eine Neustart-Schleife: fuer den Fall, dass die Notseite ausfaellt, wartet der Container.
    assert "exec sleep 2147483647" in text
    assert not any(re.search(r"\bwhile\b.*\bboot\b|\bfor\b.*\bboot\b", line) for line in commands), "boot wird nie in einer Schleife wiederholt"


@needs_sh
@pytest.mark.skipif(os.getuid() != 0 or shutil.which("setpriv") is None or shutil.which("find") is None, reason="braucht root und echtes setpriv")
def test_with_the_real_programs_as_root():
    # Nicht tmp_path: dessen Ordner sind 0700 (nur root), der Benutzer 1000 kaeme nicht hinein.
    base = Path(tempfile.mkdtemp(prefix="entrypoint-test-"))
    base.chmod(0o755)
    try:
        _real_programs_as_root(Setup(base))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _real_programs_as_root(setup: Setup) -> None:
    for name in ("id", "chown", "setpriv"):
        (setup.bin / name).unlink()
    probe = "grep -E '^(Uid|NoNewPrivs):' /proc/self/status | tr '\\t\\n' '  ' >> \"$STUB_LOG.steps\"; echo >> \"$STUB_LOG.steps\""
    _executable(setup.bin / "app", f"#!/bin/sh\n{probe}\n")
    _executable(setup.bin / "python", "#!/bin/sh\nexit 0\n")
    # Der Benutzer 1000 muss das Protokoll beschreiben duerfen.
    Path(f"{setup.log}.steps").touch()
    Path(f"{setup.log}.steps").chmod(0o666)
    # Ohne Benutzer `lattice` in der Testumgebung greift der Rueckfall 1000:1000.
    result = setup.run(uid=0)
    assert result.returncode == 0, result.stderr
    assert (setup.data / "lattice.db").stat().st_uid == 1000
    assert (setup.data / "unterordner" / "datei.txt").stat().st_gid == 1000
    assert (setup.data / "link-nach-draussen").lstat().st_uid == 1000, "der Link selbst gehoert jetzt lattice ..."
    assert (setup.outside / "wichtig.txt").stat().st_uid == 0, "... sein Ziel draussen nicht"
    steps = setup.lines("steps")
    assert steps and "Uid: 1000 1000 1000 1000" in steps[-1] and "NoNewPrivs: 1" in steps[-1]


def test_dockerfile_hands_over_to_the_entrypoint_and_checks_setpriv_at_build_time():
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "\nUSER " not in code, "kein USER: das Skript startet als root, gibt die Rechte ab und startet dann die Anwendung"
    assert "setpriv" in code, "Bauzeit-Pruefung: fehlt setpriv im Basis-Image, schlaegt schon der Build fehl"
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in code


def test_dockerfile_healthcheck_waits_long_enough_for_a_slow_migration_but_not_forever():
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r"HEALTHCHECK[^\n]*--start-period=(\d+)s", code)
    assert match, "HEALTHCHECK mit --start-period"
    assert 300 <= int(match.group(1)) <= 900, "eine lange Migration (Kopie + Umbau grosser Tabellen) darf nicht als 'unhealthy' gelten"
    assert "/api/v1/health" in code, "die Notseite antwortet dort mit 503: der Healthcheck schlaegt dann an"


def test_dockerfile_starts_uvicorn_with_a_small_websocket_message_limit():
    # uvicorn laesst WebSocket-Nachrichten bis 16 MiB zu; /ws nimmt im Anmeldefenster auch ohne Konto eine erste Nachricht an.
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r'^CMD \[(.*)\]\s*$', code, re.MULTILINE)
    assert match, "CMD in der Listenschreibweise"
    args = re.findall(r'"([^"]*)"', match.group(1))
    assert args[0] == "uvicorn"
    assert "--ws-max-size" in args, "ohne den Schalter gilt der uvicorn-Standard von 16 MiB je Nachricht"
    limit = int(args[args.index("--ws-max-size") + 1])
    assert 64 * 1024 <= limit <= 1024**2, "Anmeldung, Terminal-Eingaben und Ereignisse brauchen weit weniger als 1 MiB"
    assert args[args.index("--host") + 1] == "0.0.0.0" and args[args.index("--port") + 1] == "8080"


@needs_sh
def test_after_the_switch_home_is_not_roots_home(setup):
    # Als lattice mit HOME=/root scheiterte asyncssh an ~/.ssh/crt (kein Leserecht auf /root) -> jede SSH-Verbindung brach ab.
    _executable(setup.bin / "app", 'echo "app home=$HOME" >> "$STUB_LOG.steps"\n')
    result = setup.run(uid=0, HOME="/root")
    assert result.returncode == 0, result.stderr
    homes = [line for line in setup.lines("steps") if line.startswith("app home=")]
    assert homes and homes[0] != "app home=/root", homes
    assert homes[0] == "app home=/app" or homes[0].startswith("app home=/"), homes


# --- Rechte: umask und Aufraeumen vorhandener Dateien ---------------------------------------------------------

CHMOD_STUB = """#!/bin/sh
echo "uid=$STUB_UID $*" >> "$STUB_LOG.chmod"
exec /bin/chmod "$@"
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@needs_sh
@pytest.mark.parametrize("uid", [0, 1000])
def test_boot_and_app_start_with_a_closed_umask(setup, uid):
    # Sonst entstehen Datenbank, Dokumente und Zwischendateien mit 0644/0755 und sind im eingebundenen Hostordner fuer jeden lesbar.
    _executable(setup.bin / "app", 'echo "app umask=$(umask)" >> "$STUB_LOG.steps"\n')
    stub = setup.bin / "python"
    stub.write_text(stub.read_text(encoding="utf-8").replace("#!/bin/sh\n", '#!/bin/sh\necho "python umask=$(umask)" >> "$STUB_LOG.steps"\n', 1), encoding="utf-8")
    result = setup.run(uid=uid)
    assert result.returncode == 0, result.stderr
    steps = setup.lines("steps")
    assert "python umask=0077" in steps and "app umask=0077" in steps, steps


@needs_sh
@pytest.mark.parametrize("uid", [0, 1000])
def test_existing_open_files_in_the_data_folder_are_closed_once_at_start(setup, uid):
    (setup.data).chmod(0o755)
    (setup.data / "lattice.db").chmod(0o644)
    (setup.data / "unterordner").chmod(0o755)
    (setup.data / "unterordner" / "datei.txt").chmod(0o666)
    tool = setup.data / "unterordner" / "programm.sh"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    # Ziel eines Links aus dem Datenordner: darf nicht angefasst werden.
    setup.outside.chmod(0o755)
    (setup.outside / "wichtig.txt").chmod(0o644)
    result = setup.run(uid=uid)
    assert result.returncode == 0, result.stderr
    assert _mode(setup.data) == 0o700
    assert _mode(setup.data / "lattice.db") == 0o600
    assert _mode(setup.data / "unterordner") == 0o700
    assert _mode(setup.data / "unterordner" / "datei.txt") == 0o600
    assert _mode(tool) == 0o700, "das Ausfuehrungsrecht des Besitzers bleibt"
    assert _mode(setup.outside) == 0o755 and _mode(setup.outside / "wichtig.txt") == 0o644, "ein Link nach draussen wird nicht verfolgt"


@needs_sh
def test_rights_are_set_by_the_image_user_never_by_root(setup):
    # Ein als root laufendes chmod koennte ein zwischen Suche und Aenderung untergeschobener Link auf fremde Dateien lenken.
    _executable(setup.bin / "chmod", CHMOD_STUB)
    (setup.data / "lattice.db").chmod(0o644)
    result = setup.run(uid=0)
    assert result.returncode == 0, result.stderr
    calls = setup.lines("chmod")
    assert calls, "chmod lief"
    image_uid = os.getuid() + 1
    assert all(call.startswith(f"uid={image_uid} ") for call in calls), calls


@needs_sh
def test_a_data_folder_that_is_a_link_is_left_alone(setup, tmp_path):
    # Ist /app/data selbst ein Link, wird dem Link nicht gefolgt (weder chmod noch find).
    target = tmp_path / "anderswo"
    target.mkdir()
    target.chmod(0o755)
    (target / "datei").write_text("x", encoding="utf-8")
    (target / "datei").chmod(0o644)
    real = setup.data
    moved = tmp_path / "echt"
    real.rename(moved)
    real.symlink_to(target)
    result = setup.run(uid=1000)
    assert result.returncode == 0, result.stderr
    assert _mode(target) == 0o755 and _mode(target / "datei") == 0o644


@needs_sh
def test_failing_chmod_only_warns_and_the_app_still_starts(setup):
    _executable(setup.bin / "chmod", "#!/bin/sh\necho 'chmod: Operation not permitted' >&2\nexit 1\n")
    result = setup.run(uid=1000)
    assert result.returncode == 0, result.stderr
    assert "Warnung" in result.stderr
    assert setup.lines("steps")[-1].startswith("app uid=")


def test_dockerfile_hands_only_the_data_folder_to_the_image_user():
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    flat = re.sub(r"\\\n\s*", " ", code)
    assert not re.search(r"chown\s+-R\s+lattice", flat), "Code unter /app darf dem Dienstbenutzer nicht gehoeren"
    assert "chown lattice:lattice /app/data" in flat, "nur der Datenordner gehoert lattice"
    assert not re.search(r"chown\s+(-\w+\s+)*lattice(:lattice)?\s+/app(\s|$)", flat), "auch /app selbst nicht"
    assert "chown -R root:root /app" in flat and "go-w" in flat, "alles andere ist root-eigen und nicht fuer andere beschreibbar"
    assert "chmod 700 /app/data" in flat
    # `lattice` darf keine `__pycache__` mehr anlegen: was zur Laufzeit importiert wird und nicht in site-packages liegt,
    # uebersetzt schon der Build (Erweiterungen, Migrationen); sonst wuerde es bei jedem Start neu uebersetzt.
    compile_step = re.search(r"python -m compileall[^\n&|]*", flat)
    assert compile_step, "compileall im Build"
    assert "/app/extensions" in compile_step.group(0) and "/app/backend/migrations" in compile_step.group(0)


def _healthcheck_python_args(command: list[str] | str) -> list[str]:
    parts = command.split() if isinstance(command, str) else list(command)
    index = parts.index("python")
    return parts[index + 1 : index + 3]


def test_dockerfile_healthcheck_runs_python_isolated():
    # Der Check laeuft als root im Ordner /app: ohne `-I` stuende der aktuelle Ordner vorn im Suchpfad.
    text = DOCKERFILE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r"HEALTHCHECK[^\n]*\\\n\s*CMD\s+(python[^\n]*)", code)
    assert match, "HEALTHCHECK mit CMD python"
    assert _healthcheck_python_args(match.group(1)) == ["-I", "-c"]


@pytest.mark.parametrize("name", ["docker-compose.yml", "compose.standalone.yml"])
def test_compose_healthchecks_run_python_isolated(name):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((ROOT / "deploy" / name).read_text(encoding="utf-8"))
    test = data["services"]["nodvard-deck"]["healthcheck"]["test"]
    assert test[0] == "CMD" and _healthcheck_python_args(test[1:]) == ["-I", "-c"]
