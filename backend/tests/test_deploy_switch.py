"""deploy/pi_switch.sh (Schaltschritt auf dem Pi) und sein Zusammenspiel mit scripts/deploy_pi.sh.

Die Skripte laufen echt per Bash; `docker` und `curl` sind nachgebaute Python-Programme im PATH,
die Images und Container in einer JSON-Datei fuehren (kein Docker, kein Netzwerk). Geprueft wird
vor allem der Uebergang vom alten Namen (Image `lattice:latest`, Container `deploy-lattice-1`)
auf `nodvard-deck`:

- erster Uebergang: der alte Container wird nur GESTOPPT, sein Image wird `nodvard-deck:previous`;
  geloescht wird er erst von `cleanup`, nachdem alles geprueft ist;
- Folge-Deploy, Rollback (auch ohne Compose), frische Maschine;
- nichts veraendern, wenn Docker/Compose/Image nicht in Ordnung sind (Exit 3);
- falsches (leeres) Volume, leere Daten, eine Waise, eine Neustart-Schleife: nie "OK", immer Rollback;
- ein Rollback, der selbst nicht klappt, meldet ROLLBACK-FAIL.

Die Compose-Datei selbst prueft backend/tests/test_deploy_compose.py.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SWITCH = ROOT / "deploy" / "pi_switch.sh"
DEPLOY = ROOT / "scripts" / "deploy_pi.sh"

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="braucht eine echte POSIX-Shell (und Python-Skripte als docker-/curl-Attrappe im PATH)",
)

OLD, NEW, BAD, MID, OLDER, LOOP = "sha256:old", "sha256:new", "sha256:bad", "sha256:mid", "sha256:older", "sha256:loop"
VOL = "deploy_lattice_data"
OLD_C, NEW_C = "deploy-lattice-1", "deploy-nodvard-deck-1"

# `docker`-Attrappe. Zustand: {"images": {ref: id}, "containers": {name: {"image", "running", ...}}}.
DOCKER_STUB = r'''#!__PYTHON__
import json, os, signal, sys

STATE = os.environ["STUB_STATE"]
with open(STATE + ".calls", "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
with open(STATE, encoding="utf-8") as fh:
    st = json.load(fh)
args = sys.argv[1:]
OLD_C = "deploy-lattice-1"
NEW_C = "deploy-nodvard-deck-1"
env = os.environ.get


def save():
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh)


def fmt_of(rest):
    for flag in ("-f", "--format"):
        if flag in rest:
            return rest[rest.index(flag) + 1]
    return None


def out(value):
    sys.stdout.write(value + "\n")


def state_of(c):
    if c.get("restarting"):
        return "restarting"
    return c.get("status") or ("running" if c["running"] else "exited")


cmd = args[0] if args else ""
rest = args[1:]
if cmd == "info":
    sys.exit(1 if env("STUB_DOCKER_DOWN") == "1" else 0)
elif cmd == "image" and rest and rest[0] == "inspect":
    ref = rest[-1]
    fmt = fmt_of(rest)
    if fmt and "Architecture" in fmt:
        out("arm64")
        sys.exit(0)
    if ref not in st["images"]:
        sys.stderr.write("Error: No such image: %s\n" % ref)
        sys.exit(1)
    out(st["images"][ref] if fmt else json.dumps([{"Id": st["images"][ref]}]))
elif cmd == "container" and rest and rest[0] == "inspect":
    name = rest[-1]
    fmt = fmt_of(rest)
    if name not in st["containers"]:
        sys.stderr.write("Error: No such container: %s\n" % name)
        sys.exit(1)
    c = st["containers"][name]
    if fmt and "Mounts" in fmt:
        out(c.get("volume", "deploy_lattice_data"))
    elif fmt and "Restarting" in fmt:
        out("true" if c.get("restarting") else "false")
    elif fmt and "Status" in fmt:
        out(state_of(c))
    elif fmt and ".Image" in fmt:
        out(c["image"])
    else:
        out(json.dumps([c]))
elif cmd == "tag":
    src, dst = rest
    if src not in st["images"]:
        sys.stderr.write("Error: No such image: %s\n" % src)
        sys.exit(1)
    st["images"][dst] = st["images"][src]
    save()
elif cmd == "stop":
    name = rest[-1]
    if env("STUB_HUP_PARENT") == "1":
        os.kill(os.getppid(), signal.SIGHUP)
    if env("STUB_HUP_PID_FILE"):
        # Leitungsabbruch: die aeussere Shell (ssh-Sitzung) bekommt SIGHUP.
        with open(env("STUB_HUP_PID_FILE"), encoding="utf-8") as pid_fh:
            os.kill(int(pid_fh.read().strip()), signal.SIGHUP)
    # STUB_STICKY_OLD=1: der alte Container laesst sich nicht stoppen (haengt).
    if name in st["containers"] and not (env("STUB_STICKY_OLD") == "1" and name == OLD_C):
        st["containers"][name]["running"] = False
        save()
elif cmd == "start":
    name = rest[-1]
    if name not in st["containers"] or env("STUB_START_FAILS") == "1":
        sys.stderr.write("Error: cannot start %s\n" % name)
        sys.exit(1)
    st["containers"][name]["running"] = True
    st["containers"][name].pop("restarting", None)
    save()
elif cmd == "rm":
    st["containers"].pop(rest[-1], None)
    save()
elif cmd == "rmi":
    if env("STUB_RMI_FAILS") == "1":
        sys.stderr.write("Error: conflict: unable to remove image\n")
        sys.exit(1)
    st["images"].pop(rest[-1], None)
    save()
elif cmd == "images":
    repo = rest[0]
    for ref in sorted(st["images"]):
        if ref.startswith(repo + ":"):
            out(ref.split(":", 1)[1])
elif cmd == "compose":
    if rest[:2] != ["-p", "deploy"]:
        sys.stderr.write("docker-Attrappe: compose ohne -p deploy\n")
        sys.exit(97)
    rest = rest[2:]
    if rest and rest[0] in ("stop", "start"):
        c = st["containers"].get(NEW_C)
        if c is None:
            sys.stderr.write("Error: no container found for service\n")
            sys.exit(1 if rest[0] == "start" else 0)
        c["running"] = rest[0] == "start"
        save()
    elif rest and rest[0] == "run":
        sys.exit(1 if env("STUB_RUN_FAILS") == "1" else 0)
    elif rest and rest[0] == "config":
        sys.exit(1 if env("STUB_COMPOSE_CONFIG_FAIL") == "1" else 0)
    elif rest and rest[0] == "up":
        image = st["images"].get("nodvard-deck:latest")
        if image is None:
            sys.stderr.write("Error: pull access denied for nodvard-deck\n")
            sys.exit(1)
        old = st["containers"].get(OLD_C)
        port_taken = bool(old and old["running"])
        c = {"image": image, "running": not port_taken, "volume": env("STUB_COMPOSE_VOLUME") or "deploy_lattice_data"}
        if port_taken or image == "sha256:bad":
            c["running"] = False
            c["status"] = "created" if port_taken else "exited"
        if image == "sha256:loop":
            c["running"], c["restarting"] = True, True
        st["containers"][NEW_C] = c
        save()
        if port_taken and env("STUB_COMPOSE_EXIT0") != "1":
            sys.stderr.write("Error: Bind for 0.0.0.0:8080 failed: port is already allocated\n")
            sys.exit(1)
    else:
        sys.stderr.write("docker-Attrappe kennt nicht: compose %s\n" % " ".join(rest))
        sys.exit(99)
elif cmd == "load":
    tag = sys.stdin.read().strip()
    if tag and tag not in st["images"]:
        st["images"][tag] = env("STUB_NEW_ID", "sha256:new")
        save()
elif cmd == "save":
    out(rest[-1])
elif cmd in ("logs", "image", "builder"):
    pass
else:
    sys.stderr.write("docker-Attrappe kennt nicht: %s\n" % " ".join(args))
    sys.exit(99)
'''

# `curl`-Attrappe: /health und /auth/bootstrap antworten wie der Dienst, der gerade laeuft.
CURL_STUB = r'''#!__PYTHON__
import json, os, sys

with open(os.environ["STUB_STATE"], encoding="utf-8") as fh:
    st = json.load(fh)
url = sys.argv[-1]
with open(os.environ["STUB_STATE"] + ".curl", "a", encoding="utf-8") as fh:
    fh.write(url + "\n")
with_code = "-w" in sys.argv


def answer(body, code=200):
    print(body + ("\n%d" % code if with_code else ""))


running = {n: c for n, c in st["containers"].items() if c["running"] and not c.get("restarting")}

def no_answer():
    if with_code:
        print("\n000")
    sys.exit(7)


restarted = False
if os.environ.get("STUB_NO_HEALTH_AFTER_START") == "1":
    with open(os.environ["STUB_STATE"] + ".calls", encoding="utf-8") as fh:
        restarted = "start deploy-lattice-1" in fh.read().splitlines()
if not running or os.environ.get("STUB_NO_HEALTH") == "1" or restarted:
    no_answer()
name, c = sorted(running.items())[0]
if os.environ.get("STUB_UNHEALTHY") == c["image"]:
    no_answer()
# Notseite (nodvard_deck.rescue): STUB_RESCUE=<Image> -- dieses Image antwortet mit der Notseite; STUB_RESCUE_AFTER_START=1 --
# der alte Container antwortet nach dem Zurueckstarten damit (seine Daten sind fuer ihn zu neu).
rescue = os.environ.get("STUB_RESCUE") == c["image"]
if os.environ.get("STUB_RESCUE_AFTER_START") == "1" and name == "deploy-lattice-1":
    with open(os.environ["STUB_STATE"] + ".calls", encoding="utf-8") as fh:
        rescue = "start deploy-lattice-1" in fh.read().splitlines()
if rescue:
    if url.endswith("/api/v1/health"):
        answer('{"status":"rescue"}', 503)
    else:
        answer('{"status":"rescue","detail":"Nodvard Deck laeuft im Notfallmodus."}', 503)
    sys.exit(0)
if url.endswith("/api/v1/health"):
    answer('{"status":"ok"}')
elif url.endswith("/api/v1/auth/bootstrap"):
    if os.environ.get("STUB_BOOTSTRAP_404") == c["image"]:
        answer('{"detail":"Not Found"}', 404)  # altes Image ohne den Endpunkt
    else:
        empty = os.environ.get("STUB_EMPTY_DATA") == "1" and name == "deploy-nodvard-deck-1"
        answer('{"needed":%s}' % ("true" if empty else "false"))
else:
    sys.exit(22)
'''

SSH_STUB = """#!/usr/bin/env bash
# Attrappe: das letzte Argument ist der Befehl auf dem Pi; er laeuft lokal mit eigenem HOME.
cmd="${@: -1}"
echo "$cmd" >> "$STUB_STATE.ssh"
if [ -n "${STUB_SSH_FAIL:-}" ] && [[ "$cmd" == *"$STUB_SSH_FAIL"* ]]; then exit 255; fi
case "$cmd" in
  hostname*) echo pi-attrappe; exit 0 ;;
esac
HOME="$STUB_HOME" exec bash -c "$cmd"
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Machine:
    """Ein Zustand fuer `docker`/`curl` (Attrappen) samt Aufruf der Skripte."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _executable(self.bin / "docker", DOCKER_STUB.replace("__PYTHON__", sys.executable))
        _executable(self.bin / "curl", CURL_STUB.replace("__PYTHON__", sys.executable))
        self.state_file = tmp_path / "docker-state.json"
        self.set(images={}, containers={})

    def set(self, images: dict[str, str], containers: dict[str, dict]) -> None:
        self.state_file.write_text(json.dumps({"images": images, "containers": containers}), encoding="utf-8")

    @property
    def images(self) -> dict[str, str]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))["images"]

    @property
    def containers(self) -> dict[str, dict]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))["containers"]

    @property
    def calls(self) -> list[str]:
        path = Path(str(self.state_file) + ".calls")
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def env(self, **extra: str) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith("STUB_") and k != "COMPOSE_PROJECT_NAME"}
        return {**base, "PATH": f"{self.bin}{os.pathsep}{base['PATH']}", "STUB_STATE": str(self.state_file), "PI_SWITCH_LOG_DIR": str(self.tmp / "logs"), **extra}

    def switch(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SWITCH), *args], env=self.env(**env), capture_output=True, text=True, timeout=60, check=False)

    def compose_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith("compose")]


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


def _running(image: str = OLD, **extra) -> dict:
    return {"image": image, "running": True, **extra}


def _old_running() -> dict:
    """Ausgangslage vor der Umbenennung: Image lattice:latest und Container deploy-lattice-1 laufen."""
    return {OLD_C: _running(OLD)}


def _first_transition(machine: Machine, new: str = NEW) -> None:
    machine.set(images={"lattice:latest": OLD, "nodvard-deck:pi-abc": new}, containers=_old_running())


# ---------------------------------------------------------------------------
# pi_switch.sh: switch
# ---------------------------------------------------------------------------
@needs_bash
def test_a_first_transition_stops_the_old_container_but_keeps_it(machine):
    _first_transition(machine)
    result = machine.switch("switch", "nodvard-deck:pi-abc")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Erster Uebergang" in result.stdout and "VERIFY-OK" in result.stdout
    assert machine.images["nodvard-deck:previous"] == OLD
    assert machine.images["nodvard-deck:latest"] == NEW
    assert machine.images["lattice:latest"] == OLD
    # Der alte Container ist nur gestoppt, nicht geloescht; der neue laeuft mit dem alten Volume.
    assert machine.containers[OLD_C]["running"] is False
    assert machine.containers[NEW_C] == {"image": NEW, "running": True, "volume": VOL}
    calls = machine.calls
    assert not any(c.startswith(("rm", "rmi")) for c in calls), "switch loescht nichts"
    assert calls.index("tag lattice:latest nodvard-deck:previous") < calls.index("stop -t 30 deploy-lattice-1") < calls.index("compose -p deploy up -d --no-build")
    # Keine Projekt-weiten Eingriffe, kein Compose ohne festes Projekt, nichts, was Daten beruehrt.
    assert not any("--remove-orphans" in c or " down" in c or c.startswith("volume") or " -v" in c for c in calls), calls
    assert all(c.startswith("compose -p deploy ") for c in machine.compose_calls())


@needs_bash
def test_a_cleanup_removes_the_old_container_but_keeps_the_old_images_while_they_are_the_way_back(machine):
    _first_transition(machine)
    assert machine.switch("switch", "nodvard-deck:pi-abc").returncode == 0
    result = machine.switch("cleanup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEANUP-OK" in result.stdout
    assert OLD_C not in machine.containers and NEW_C in machine.containers
    # nodvard-deck:previous ist noch dasselbe Image wie lattice:latest: die alten Tags bleiben.
    assert machine.images["lattice:latest"] == OLD and machine.images["nodvard-deck:previous"] == OLD
    assert "Alte lattice:*-Images bleiben" in result.stdout
    # Die Tags hinter latest liegen bleiben (hier: pi-abc ist der neue Stand).
    assert "nodvard-deck:pi-abc" in machine.images


@needs_bash
def test_a_cleanup_after_a_later_deploy_removes_old_images_and_unused_pi_tags(machine):
    machine.set(
        images={
            "lattice:latest": OLD, "lattice:previous": OLDER, "lattice:pi-x": OLDER,
            "nodvard-deck:latest": MID, "nodvard-deck:previous": OLDER, "nodvard-deck:pi-old": "sha256:weg",
            "nodvard-deck:pi-mid": MID, "nodvard-deck:pi-older": OLDER,
        },
        containers={NEW_C: _running(MID, volume=VOL)},
    )
    result = machine.switch("cleanup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.images == {
        "nodvard-deck:latest": MID, "nodvard-deck:previous": OLDER, "nodvard-deck:pi-mid": MID, "nodvard-deck:pi-older": OLDER,
    }
    assert NEW_C in machine.containers


@needs_bash
def test_cleanup_refuses_while_the_new_container_is_not_fine_and_keeps_the_old_one(machine):
    machine.set(
        images={"lattice:latest": OLD, "nodvard-deck:latest": NEW, "nodvard-deck:previous": OLD},
        containers={OLD_C: {"image": OLD, "running": False}, NEW_C: {"image": NEW, "running": False, "status": "exited", "volume": VOL}},
    )
    result = machine.switch("cleanup")
    assert result.returncode != 0 and "FAIL" in result.stderr
    assert OLD_C in machine.containers
    assert not any(c.startswith(("rm", "rmi")) for c in machine.calls)


@needs_bash
def test_b_follow_up_deploy(machine):
    machine.set(
        images={"nodvard-deck:latest": MID, "nodvard-deck:previous": OLDER, "lattice:latest": OLD, "nodvard-deck:pi-def": NEW},
        containers={NEW_C: _running(MID, volume=VOL)},
    )
    result = machine.switch("switch", "nodvard-deck:pi-def")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.images["nodvard-deck:previous"] == MID, "nodvard-deck:latest wird zur Sicherung, nicht lattice:latest"
    assert machine.images["nodvard-deck:latest"] == NEW
    assert machine.containers[NEW_C] == {"image": NEW, "running": True, "volume": VOL}
    assert "tag lattice:latest nodvard-deck:previous" not in machine.calls
    assert not any(c.startswith(("stop", "rm")) for c in machine.calls)


@needs_bash
def test_b_same_image_again_keeps_the_way_back(machine):
    # Nach einem Abbruch wird derselbe Stand erneut ausgeliefert: die Sicherung darf nicht
    # mit dem fraglichen neuen Stand ueberschrieben werden.
    machine.set(
        images={"nodvard-deck:latest": NEW, "nodvard-deck:previous": MID, "nodvard-deck:pi-def": NEW},
        containers={NEW_C: _running(NEW, volume=VOL)},
    )
    result = machine.switch("switch", "nodvard-deck:pi-def")
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.images["nodvard-deck:previous"] == MID


# ---------------------------------------------------------------------------
# Nichts veraendern, wenn die Voraussetzungen fehlen (Exit 3)
# ---------------------------------------------------------------------------
def _nothing_changed(machine: Machine, before: dict) -> None:
    assert machine.images == before["images"] and machine.containers == before["containers"]
    assert not any(c.startswith(("tag", "stop", "start", "rm", "rmi", "compose -p deploy up")) for c in machine.calls), machine.calls


@needs_bash
@pytest.mark.parametrize(
    ("env", "tag", "message"),
    [
        ({}, "nodvard-deck:pi-fehlt", "nicht vorhanden"),
        ({"STUB_DOCKER_DOWN": "1"}, "nodvard-deck:pi-abc", "Docker ist nicht erreichbar"),
        ({"STUB_COMPOSE_CONFIG_FAIL": "1"}, "nodvard-deck:pi-abc", "Compose-Datei"),
    ],
    ids=["image-fehlt", "docker-nicht-erreichbar", "compose-config-scheitert"],
)
def test_c_switch_changes_nothing_and_exits_3_when_a_precondition_fails(machine, env, tag, message):
    _first_transition(machine)
    before = {"images": machine.images, "containers": machine.containers}
    result = machine.switch("switch", tag, **env)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "FAIL" in result.stderr and message in result.stderr
    _nothing_changed(machine, before)
    assert machine.containers[OLD_C]["running"] is True, "der alte Container laeuft unbeeinflusst weiter"


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
@needs_bash
def test_c_failed_first_transition_rolls_back_by_starting_the_old_container_without_compose(machine):
    _first_transition(machine, BAD)
    failed = machine.switch("switch", "nodvard-deck:pi-abc")
    assert failed.returncode != 0
    assert "FAIL" in failed.stderr and "laeuft nicht" in failed.stderr
    assert machine.containers[NEW_C]["running"] is False and machine.containers[OLD_C]["running"] is False

    compose_before = len(machine.compose_calls())
    back = machine.switch("rollback")
    assert back.returncode == 0, back.stdout + back.stderr
    assert "VERIFY-OK" in back.stdout
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}, "neuer Container weg, alter laeuft wieder"
    assert len(machine.compose_calls()) == compose_before, "der Rollback braucht Compose nicht"
    assert "start deploy-lattice-1" in machine.calls
    assert machine.images["nodvard-deck:latest"] == OLD, "auch das Tag zeigt wieder auf den alten Stand"


@needs_bash
def test_c_rollback_after_a_follow_up_deploy_returns_to_the_last_good_image_via_compose(machine):
    machine.set(
        images={"nodvard-deck:latest": MID, "nodvard-deck:pi-bad": BAD},
        containers={NEW_C: _running(MID, volume=VOL)},
    )
    assert machine.switch("switch", "nodvard-deck:pi-bad").returncode != 0
    back = machine.switch("rollback")
    assert back.returncode == 0, back.stdout + back.stderr
    assert machine.containers[NEW_C] == {"image": MID, "running": True, "volume": VOL}


@needs_bash
def test_c_old_container_that_cannot_be_started_makes_the_rollback_fail(machine):
    _first_transition(machine, BAD)
    assert machine.switch("switch", "nodvard-deck:pi-abc").returncode != 0
    back = machine.switch("rollback", STUB_START_FAILS="1")
    assert back.returncode != 0


@needs_bash
def test_d_fresh_machine_without_any_image(machine):
    machine.set(images={"nodvard-deck:pi-abc": NEW}, containers={})
    result = machine.switch("switch", "nodvard-deck:pi-abc")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Frische Maschine" in result.stdout
    assert "nodvard-deck:previous" not in machine.images
    assert machine.containers == {NEW_C: {"image": NEW, "running": True, "volume": VOL}}
    assert not any(c.startswith("stop") for c in machine.calls)

    # Ohne Sicherung gibt es auch keinen Rueckweg -- das sagt das Skript ehrlich.
    nothing = machine.switch("rollback")
    assert nothing.returncode != 0 and "FAIL" in nothing.stderr
    assert machine.containers[NEW_C]["image"] == NEW, "der Rollback ohne Sicherung laesst alles, wie es ist"


# ---------------------------------------------------------------------------
# Was "laeuft" wirklich heisst
# ---------------------------------------------------------------------------
@needs_bash
def test_e_old_container_that_cannot_be_stopped_fails_the_switch_and_is_never_deleted(machine):
    _first_transition(machine)
    result = machine.switch("switch", "nodvard-deck:pi-abc", STUB_STICKY_OLD="1")
    assert result.returncode != 0
    assert OLD_C in machine.containers and machine.containers[OLD_C]["running"] is True
    assert not any(c.startswith("rm") for c in machine.calls)


@needs_bash
def test_e_orphan_that_keeps_running_is_a_fail_even_if_compose_says_ok(machine):
    # Der gefaehrliche Fall: compose meldet 0, der neue Container startet wegen des belegten
    # Ports nicht, der alte antwortet weiter auf /api/v1/health.
    _first_transition(machine)
    result = machine.switch("switch", "nodvard-deck:pi-abc", STUB_STICKY_OLD="1", STUB_COMPOSE_EXIT0="1")
    assert result.returncode != 0
    assert "FAIL" in result.stderr and "Waise" in result.stderr
    assert "VERIFY-OK" not in result.stdout


@needs_bash
def test_e_verify_fails_for_a_running_old_container_next_to_a_healthy_new_one(machine):
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL), OLD_C: _running(OLD)})
    result = machine.switch("verify")
    assert result.returncode != 0 and "Waise" in result.stderr


@needs_bash
def test_e_a_stopped_old_container_next_to_the_new_one_is_fine(machine):
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL), OLD_C: {"image": OLD, "running": False}})
    assert machine.switch("verify").returncode == 0


@needs_bash
def test_f_running_with_the_wrong_volume_is_a_fail_and_rolls_back(machine):
    # Leeres Volume (z. B. anderer Projektname): Container laeuft, Health waere ok, aber die Daten fehlen.
    _first_transition(machine)
    result = machine.switch("switch", "nodvard-deck:pi-abc", STUB_COMPOSE_VOLUME="foo_lattice_data")
    assert result.returncode != 0
    assert "foo_lattice_data" in result.stderr and "deploy_lattice_data" in result.stderr and "leeres Volume" in result.stderr
    back = machine.switch("rollback")
    assert back.returncode == 0, back.stdout + back.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
def test_g_verify_needs_status_running_and_no_restart_loop(machine):
    machine.set(images={"nodvard-deck:latest": LOOP}, containers={NEW_C: _running(LOOP, volume=VOL, restarting=True)})
    looping = machine.switch("verify")
    assert looping.returncode != 0 and "laeuft nicht" in looping.stderr and "restarting" in looping.stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: {"image": NEW, "running": True, "status": "paused", "volume": VOL}})
    assert "paused" in machine.switch("verify").stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: {"image": NEW, "running": False, "status": "exited", "volume": VOL}})
    assert machine.switch("verify").returncode != 0
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(OLD, volume=VOL)})
    assert "anderen Image" in machine.switch("verify").stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={})
    assert "existiert nicht" in machine.switch("verify").stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})
    assert machine.switch("verify").returncode == 0


@needs_bash
def test_g_a_restart_loop_after_switch_fails_it(machine):
    _first_transition(machine, LOOP)
    result = machine.switch("switch", "nodvard-deck:pi-abc")
    assert result.returncode != 0 and "restarting" in result.stderr


@needs_bash
def test_h_a_hangup_in_the_middle_does_not_kill_the_switch(machine):
    # ssh bricht ab -> SIGHUP an die Shell, waehrend sie mitten im Umschalten ist.
    _first_transition(machine)
    result = machine.switch("switch", "nodvard-deck:pi-abc", STUB_HUP_PARENT="1")
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "VERIFY-OK" in result.stdout


@needs_bash
def test_unknown_subcommand_is_refused(machine):
    assert machine.switch("unsinn").returncode == 2


@needs_bash
def test_compose_project_name_from_the_environment_cannot_override_the_project(machine):
    _first_transition(machine)
    env = machine.env(COMPOSE_PROJECT_NAME="foo")
    result = subprocess.run(["bash", str(SWITCH), "switch", "nodvard-deck:pi-abc"], env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(c.startswith("compose -p deploy ") for c in machine.compose_calls())


# ---------------------------------------------------------------------------
# Liegen gebliebener alter Container, Aufraeumen, Vorabpruefung, abgekoppelter Aufruf
# ---------------------------------------------------------------------------
@needs_bash
def test_rollback_ignores_a_stale_old_container_with_another_image_and_uses_compose(machine):
    # deploy-lattice-1 ist ein Ueberbleibsel (uraltes Image), nodvard-deck:previous ein anderer Stand.
    machine.set(
        images={"nodvard-deck:latest": BAD, "nodvard-deck:previous": MID},
        containers={OLD_C: {"image": OLDER, "running": False}, NEW_C: {"image": BAD, "running": False, "status": "exited", "volume": VOL}},
    )
    back = machine.switch("rollback")
    assert back.returncode == 0, back.stdout + back.stderr
    assert "start deploy-lattice-1" not in machine.calls
    assert machine.containers[OLD_C] == {"image": OLDER, "running": False}, "das Ueberbleibsel bleibt, wie es ist"
    assert machine.containers[NEW_C] == {"image": MID, "running": True, "volume": VOL}
    assert "compose -p deploy up -d --no-build" in machine.calls


@needs_bash
def test_switch_removes_a_stopped_old_container_when_the_transition_had_already_succeeded(machine):
    machine.set(
        images={"nodvard-deck:latest": MID, "nodvard-deck:previous": OLDER, "lattice:latest": OLD, "nodvard-deck:pi-def": NEW},
        containers={OLD_C: {"image": OLD, "running": False}, NEW_C: _running(MID, volume=VOL)},
    )
    result = machine.switch("switch", "nodvard-deck:pi-def")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rm -f deploy-lattice-1" in machine.calls and OLD_C not in machine.containers
    assert "Liegengebliebener" in result.stdout


@needs_bash
def test_switch_keeps_the_old_container_of_a_rolled_back_first_transition(machine):
    # Erster Uebergang scheitert, der Rollback startet den alten Container wieder (laeuft, nodvard-deck:latest
    # existiert jetzt). Der naechste Versuch darf ihn nicht als Ueberbleibsel loeschen.
    _first_transition(machine, BAD)
    assert machine.switch("switch", "nodvard-deck:pi-abc").returncode != 0
    assert machine.switch("rollback").returncode == 0
    machine.set(images={**machine.images, "nodvard-deck:pi-abc": NEW}, containers=machine.containers)
    calls_before = len(machine.calls)
    result = machine.switch("switch", "nodvard-deck:pi-abc")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(c.startswith("rm") for c in machine.calls[calls_before:])
    assert machine.containers[OLD_C]["running"] is False and machine.containers[NEW_C]["image"] == NEW


@needs_bash
def test_cleanup_only_reports_removed_images_that_were_really_removed(machine):
    machine.set(
        images={"nodvard-deck:latest": MID, "nodvard-deck:previous": OLDER, "nodvard-deck:pi-old": "sha256:weg", "lattice:latest": "sha256:alt"},
        containers={NEW_C: _running(MID, volume=VOL)},
    )
    result = machine.switch("cleanup", STUB_RMI_FAILS="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "entfernt." not in result.stdout
    assert "Hinweis: nodvard-deck:pi-old liess sich nicht entfernen" in result.stdout
    assert "Hinweis: lattice:latest liess sich nicht entfernen" in result.stdout


@needs_bash
@pytest.mark.parametrize(
    ("state", "env", "expected"),
    [
        ("none", {}, "none"),
        ("old", {}, "needed=false"),
        ("new-empty", {"STUB_EMPTY_DATA": "1"}, "needed=true"),
        ("old", {"STUB_BOOTSTRAP_404": OLD}, "http=404"),
        ("old", {"STUB_UNHEALTHY": OLD}, "noanswer"),
        ("old-wrong-volume", {"STUB_BOOTSTRAP_404": OLD}, "noanswer"),
    ],
    ids=["kein-container", "eingerichtet", "nicht-eingerichtet", "404-altes-image", "keine-antwort", "404-fremdes-volume"],
)
def test_precheck_tells_what_the_running_dashboard_says(machine, state, env, expected):
    containers = {
        "none": {},
        "old": _old_running(),
        "new-empty": {NEW_C: _running(NEW, volume=VOL)},
        "old-wrong-volume": {OLD_C: _running(OLD, volume="foo_lattice_data")},
    }[state]
    machine.set(images={}, containers=containers)
    result = machine.switch("precheck", BOOTSTRAP_RETRY_SLEEP_S="0", **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == expected


@needs_bash
def test_precheck_retries_before_giving_up(machine):
    machine.set(images={}, containers=_old_running())
    result = machine.switch("precheck", BOOTSTRAP_RETRY_SLEEP_S="0", STUB_UNHEALTHY=OLD)
    assert result.stdout.strip() == "noanswer"
    assert Path(str(machine.state_file) + ".curl").read_text(encoding="utf-8").count("bootstrap") == 5


@needs_bash
def test_detached_runs_like_the_plain_command_and_returns_its_output_and_exit_code(machine):
    _first_transition(machine)
    ok = machine.switch("detached", "switch", "nodvard-deck:pi-abc")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "VERIFY-OK" in ok.stdout
    machine.set(images={"nodvard-deck:latest": MID}, containers={})
    failed = machine.switch("detached", "switch", "nodvard-deck:pi-fehlt")
    assert failed.returncode == 3 and "nicht vorhanden" in failed.stderr


@needs_bash
def test_h_a_dropped_connection_does_not_stop_a_detached_switch(machine, tmp_path):
    # Die aeussere Shell (die ssh-Sitzung) bekommt mitten im Umschalten SIGHUP und stirbt;
    # der abgekoppelte Schritt muss trotzdem zu Ende laufen (Ergebnis in pi_switch.out).
    _first_transition(machine)
    pid_file = tmp_path / "outer.pid"
    # Wurde pytest mit ignoriertem SIGHUP gestartet (nohup, manche Hintergrund-/CI-Starter), erbt die
    # aeussere Shell das: eine beim Start ignorierte Abmeldung laesst sich in einer Shell nicht mehr
    # zuruecksetzen, sie wuerde das SIGHUP einfach wegstecken (rc=0). Darum hier erst auf Standard stellen.
    proc = subprocess.Popen(
        ["bash", "-c", f'echo $$ > "{pid_file}"; bash "{SWITCH}" detached switch nodvard-deck:pi-abc; echo rc=$?'],
        env=machine.env(STUB_HUP_PID_FILE=str(pid_file)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_DFL),
    )
    stdout, stderr = proc.communicate(timeout=60)

    def log(name: str) -> str:
        path = machine.tmp / "logs" / name
        return path.read_text(encoding="utf-8") if path.exists() else "<fehlt>"

    assert proc.returncode == -signal.SIGHUP, (
        f"die aeussere Shell ist nicht an SIGHUP gestorben\nrc={proc.returncode}\nstdout={stdout!r}\nstderr={stderr!r}\n"
        f"pi_switch.out={log('pi_switch.out')!r}\npi_switch.err={log('pi_switch.err')!r}\ncalls={machine.calls}"
    )
    out = machine.tmp / "logs" / "pi_switch.out"
    # Der abgekoppelte Schritt laeuft nach dem Tod der aeusseren Shell weiter; grosszuegig warten, damit
    # auch eine ueberlastete Maschine (oder ein langsamer Pi) nicht zur Zeitgrenze wird.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not (out.exists() and "VERIFY-OK" in out.read_text(encoding="utf-8")):
        time.sleep(0.2)
    assert out.exists() and "VERIFY-OK" in out.read_text(encoding="utf-8"), f"kein VERIFY-OK: calls={machine.calls}"
    assert machine.containers[NEW_C] == {"image": NEW, "running": True, "volume": VOL}


# ---------------------------------------------------------------------------
# scripts/deploy_pi.sh zusammen mit pi_switch.sh (ssh-Attrappe fuehrt lokal aus)
# ---------------------------------------------------------------------------
@pytest.fixture
def deploy_run(tmp_path: Path, machine: Machine):
    """Temp-Repository mit deploy_pi.sh und dem echten deploy/-Ordner; liefert `run()`."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "deploy", repo / "deploy", ignore=shutil.ignore_patterns(".env*"))
    shutil.copy(DEPLOY, repo / "scripts" / "deploy_pi.sh")
    git_env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"]):
        assert subprocess.run(cmd, cwd=repo, env=git_env, capture_output=True, check=False).returncode == 0
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, env=git_env, capture_output=True, text=True, check=True).stdout.strip()
    _executable(machine.bin / "ssh", SSH_STUB)
    home = tmp_path / "pi-home"
    home.mkdir()

    def run(**extra: str) -> subprocess.CompletedProcess:
        settings = {"HEALTH_TIMEOUT_S": "10", "ROLLBACK_HEALTH_TIMEOUT_S": "5", **extra}
        env = machine.env(PI_HOST="admin@192.0.2.1", STUB_HOME=str(home), **settings)
        env.pop("DEPLOY_ROOT", None)
        return subprocess.run(["bash", str(repo / "scripts" / "deploy_pi.sh"), "--no-build"], cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)

    run.local_tag = f"nodvard-deck:pi-{sha}"
    run.home = home
    return run


def _deploy_state(machine: Machine, deploy_run, *, new: str = NEW, old: bool = True) -> None:
    machine.set(images={"lattice:latest": OLD, deploy_run.local_tag: new} if old else {deploy_run.local_tag: new}, containers=_old_running() if old else {})


@needs_bash
def test_deploy_pi_first_transition_end_to_end(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY-OK" in result.stdout and "Datencheck ok" in result.stdout and "CLEANUP-OK" in result.stdout
    assert machine.images["nodvard-deck:previous"] == OLD and machine.images["nodvard-deck:latest"] == NEW
    assert machine.containers == {NEW_C: {"image": NEW, "running": True, "volume": VOL}}, "der alte Container ist erst am Ende weg"
    calls = machine.calls
    assert calls.index("stop -t 30 deploy-lattice-1") < calls.index("compose -p deploy up -d --no-build") < calls.index("rm -f deploy-lattice-1")
    assert not any("--remove-orphans" in c for c in calls)
    # deploy/ kam mit, und die Schaltdatei liegt im bisherigen Zielordner (DEPLOY_ROOT bleibt).
    assert (deploy_run.home / "lattice-deploy-test" / "deploy" / "pi_switch.sh").exists()


@needs_bash
def test_deploy_pi_failing_new_image_rolls_back_to_the_old_container(machine, deploy_run):
    _deploy_state(machine, deploy_run, new=BAD)
    result = deploy_run()
    assert result.returncode != 0
    assert "DEPLOY-FAIL" in result.stderr and "Rollback ausgefuehrt" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "ROLLBACK-FAIL" not in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}
    assert not any(c.startswith("rm -f deploy-lattice-1") for c in machine.calls), "der alte Container wurde nie geloescht"


@needs_bash
def test_deploy_pi_never_reports_ok_while_the_old_container_holds_the_port(machine, deploy_run):
    # /health antwortet immer (wie der alte Container es taete).
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_STICKY_OLD="1", STUB_COMPOSE_EXIT0="1")
    assert result.returncode != 0
    assert "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert machine.containers[OLD_C]["running"] is True


@needs_bash
def test_deploy_pi_wrong_volume_is_rolled_back(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_COMPOSE_VOLUME="foo_lattice_data")
    assert result.returncode != 0 and "DEPLOY-OK" not in result.stdout
    assert "foo_lattice_data" in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
def test_deploy_pi_empty_data_is_rolled_back_even_if_container_and_volume_look_right(machine, deploy_run):
    # Volume-Name stimmt, aber der Dienst meldet "Einrichtung noetig", obwohl er es vorher nicht tat.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_EMPTY_DATA="1")
    assert result.returncode != 0 and "DEPLOY-OK" not in result.stdout
    assert "Datencheck" in result.stderr and "needed=true" in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
def test_deploy_pi_fresh_machine_may_need_setup(machine, deploy_run):
    # Frische Maschine: "Einrichtung noetig" ist richtig und kein Grund fuer einen Rollback.
    _deploy_state(machine, deploy_run, old=False)
    result = deploy_run(STUB_EMPTY_DATA="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY-OK" in result.stdout and "Datencheck" not in result.stdout
    assert machine.containers == {NEW_C: {"image": NEW, "running": True, "volume": VOL}}


@needs_bash
def test_deploy_pi_reports_rollback_fail_when_the_old_state_does_not_answer(machine, deploy_run):
    _deploy_state(machine, deploy_run, new=BAD)
    # Vorher antwortet der alte Dienst, nach dem Zurueckstarten nicht mehr.
    result = deploy_run(STUB_NO_HEALTH_AFTER_START="1")
    assert result.returncode != 0
    assert "ROLLBACK-FAIL" in result.stderr and "Rollback ausgefuehrt" not in result.stderr


@needs_bash
def test_deploy_pi_reports_rollback_fail_when_the_rollback_itself_fails(machine, deploy_run):
    _deploy_state(machine, deploy_run, new=BAD)
    result = deploy_run(STUB_START_FAILS="1")
    assert result.returncode != 0 and "ROLLBACK-FAIL" in result.stderr


@needs_bash
def test_deploy_pi_changes_nothing_and_does_not_roll_back_when_docker_is_not_reachable(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_DOCKER_DOWN="1")
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr
    assert "Docker ist nicht erreichbar" in result.stderr and "ROLLBACK" not in result.stderr
    assert machine.containers == _old_running()
    assert not any(c.startswith(("tag", "stop", "start", "rm")) for c in machine.calls)


@needs_bash
def test_deploy_pi_aborts_before_changing_anything_when_compose_config_fails(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_COMPOSE_CONFIG_FAIL="1")
    assert result.returncode != 0 and "Compose-Datei" in result.stderr
    assert machine.containers == _old_running()
    assert not any(c.startswith(("tag", "stop", "start", "rm")) for c in machine.calls)


@needs_bash
def test_deploy_pi_aborts_before_switching_when_the_running_dashboard_does_not_answer(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_UNHEALTHY=OLD, BOOTSTRAP_RETRY_SLEEP_S="0")
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "antwortet aber nicht" in result.stderr and "Nichts veraendert" in result.stderr
    assert machine.containers == _old_running()
    assert not any(c.startswith(("tag", "stop", "start", "rm", "compose -p deploy up")) for c in machine.calls)


@needs_bash
def test_deploy_pi_old_image_without_the_bootstrap_endpoint_counts_as_set_up(machine, deploy_run):
    # 404 vom alten Image, weil es den Endpunkt nicht kennt: der Dienst laeuft mit dem Volume, die Daten
    # gelten als vorhanden. Der neue Dienst antwortet normal (needed=false) -> Deploy ok.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_BOOTSTRAP_404=OLD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kennt /auth/bootstrap nicht" in result.stdout and "Datencheck ok" in result.stdout


@needs_bash
def test_deploy_pi_old_image_without_the_endpoint_still_gets_the_data_check_afterwards(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_BOOTSTRAP_404=OLD, STUB_EMPTY_DATA="1")
    assert result.returncode != 0 and "Datencheck" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
def test_deploy_pi_unclear_log_check_fails_without_an_automatic_rollback(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_SSH_FAIL="grep -c Traceback")
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "KEIN automatischer Rollback" in result.stderr and "auf dem Pi pruefen" in result.stderr
    assert "start deploy-lattice-1" not in machine.calls, "kein Rollback bei unklarem Zustand"
    assert machine.containers[NEW_C]["running"] is True and OLD_C in machine.containers, "nichts aufgeraeumt, nichts zurueckgeschaltet"


@needs_bash
@pytest.mark.parametrize("step", ["detached switch", "detached rollback"])
def test_deploy_pi_dropped_connection_while_switching_does_not_trigger_a_second_action(machine, deploy_run, step):
    _deploy_state(machine, deploy_run, new=BAD if step.endswith("rollback") else NEW)
    result = deploy_run(STUB_SSH_FAIL=step)
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "abgerissen" in result.stderr
    if step.endswith("switch"):
        assert not any(c.startswith(("tag", "stop", "start", "rm")) for c in machine.calls), "auf dem Pi wurde nichts angestossen"
    else:
        assert "start deploy-lattice-1" not in machine.calls and "ROLLBACK-FAIL" not in result.stderr


# ---------------------------------------------------------------------------
# backup.sh / restore.sh: auch den alten Container anhalten
# ---------------------------------------------------------------------------
@pytest.fixture
def backup_env(machine, tmp_path):
    def run(script: str, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(ROOT / "deploy" / script), *args], env=machine.env(**env), input=stdin,
            capture_output=True, text=True, timeout=60, check=False,
        )

    return run


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_and_restore_stop_a_still_running_old_container_and_start_it_again(machine, backup_env, tmp_path, script):
    machine.set(images={"nodvard-deck:latest": OLD}, containers=_old_running())
    archive = tmp_path / "sicherung.tar.gz"
    archive.write_bytes(b"x")
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = machine.calls
    assert calls.index("stop -t 30 deploy-lattice-1") < calls.index(next(c for c in calls if c.startswith("compose -p deploy run"))) < calls.index("start deploy-lattice-1")
    assert machine.containers[OLD_C]["running"] is True
    assert "compose -p deploy start nodvard-deck" not in calls


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_and_restore_keep_their_old_behaviour_when_only_the_new_service_runs(machine, backup_env, tmp_path, script):
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})
    archive = tmp_path / "sicherung.tar.gz"
    archive.write_bytes(b"x")
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = machine.calls
    assert not any(c.startswith("stop") for c in calls)
    assert calls.index("compose -p deploy stop nodvard-deck") < calls.index(next(c for c in calls if c.startswith("compose -p deploy run"))) < calls.index("compose -p deploy start nodvard-deck")


@needs_bash
def test_restore_that_fails_still_starts_the_old_container_again(machine, backup_env, tmp_path):
    machine.set(images={"nodvard-deck:latest": OLD}, containers=_old_running())
    archive = tmp_path / "sicherung.tar.gz"
    archive.write_bytes(b"x")
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_RUN_FAILS="1")
    assert result.returncode != 0
    assert machine.containers[OLD_C]["running"] is True


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_and_restore_helper_container_runs_as_the_image_user_not_as_root(machine, backup_env, tmp_path, script):
    # Das Image startet als root (der Entrypoint gibt die Rechte selbst ab). Die Hilfscontainer
    # umgehen den Entrypoint (--entrypoint tar/sh) und muessen deshalb selbst als Benutzer 1000 laufen:
    # sonst gehoerten die Sicherungsdateien auf dem Pi root statt dem normalen Benutzer.
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})
    archive = tmp_path / "sicherung.tar.gz"
    archive.write_bytes(b"x")
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    assert backup_env(script, *args, stdin="y\n").returncode == 0
    run_call = next(c for c in machine.calls if c.startswith("compose -p deploy run"))
    assert "--user 1000:1000" in run_call


# ---------------------------------------------------------------------------
# Die Notseite (nodvard_deck.rescue): Health 503 `{"status":"rescue"}` -- nie "ok", also Rollback
# ---------------------------------------------------------------------------
def _health_calls(machine: Machine) -> int:
    path = Path(str(machine.state_file) + ".curl")
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.endswith("/api/v1/health")) if path.exists() else 0


@needs_bash
def test_deploy_pi_a_new_image_that_ends_in_the_rescue_page_is_rolled_back_without_waiting_the_full_time(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    started = time.time()
    result = deploy_run(STUB_RESCUE=NEW, HEALTH_TIMEOUT_S="240")
    assert result.returncode != 0
    assert "DEPLOY-OK" not in result.stdout and "DEPLOY-FAIL" in result.stderr
    assert "Notseite" in result.stdout + result.stderr, "benennt die Ursache"
    assert "Rollback ausgefuehrt" in result.stderr and "ROLLBACK-FAIL" not in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}, "zurueck auf den alten Container"
    assert time.time() - started < 60, "die Notseite startet nie von selbst neu: die vollen 240 s abzuwarten waere sinnlos"
    assert 2 <= _health_calls(machine) <= 6


@needs_bash
def test_deploy_pi_the_rescue_page_is_never_mistaken_for_a_healthy_dashboard(machine, deploy_run):
    # Auch wenn nur ein Teil des Musters passt: "status" ohne "ok" ist nie gesund.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_RESCUE=NEW, HEALTH_TIMEOUT_S="10")
    assert result.returncode != 0 and "DEPLOY-OK" not in result.stdout
    # "healthy" steht genau einmal da: fuer den alten Stand NACH dem Rollback, nie fuer den neuen.
    assert result.stdout.count("healthy nach") == 1
    assert result.stdout.index("Notseite erkannt") < result.stdout.index("healthy nach")


@needs_bash
def test_deploy_pi_when_the_old_image_shows_the_rescue_page_after_the_rollback_the_hint_says_what_to_do(machine, deploy_run):
    # Das neue Image startet nicht; das alte findet nach dem Zurueckstarten Daten, die ihm zu neu sind: Notseite.
    _deploy_state(machine, deploy_run, new=BAD)
    result = deploy_run(STUB_RESCUE_AFTER_START="1", ROLLBACK_HEALTH_TIMEOUT_S="10")
    assert result.returncode != 0 and "ROLLBACK-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "Notfallmodus" in result.stderr and "Notfallcode" in result.stderr
    assert "Rollback ausgefuehrt" not in result.stderr


@needs_bash
def test_deploy_pi_a_dashboard_that_is_already_in_the_rescue_page_can_be_deployed_over(machine, deploy_run):
    # Der Zustand VOR dem Deploy ist die Notseite (kaputtes Update, jetzt kommt das Reparatur-Image): das darf der Weg raus sein.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_RESCUE=OLD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY-OK" in result.stdout


@needs_bash
def test_pi_switch_precheck_and_bootstrap_report_the_rescue_page_as_an_http_status_not_as_set_up(machine):
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})
    result = machine.switch("bootstrap", "1", STUB_RESCUE=NEW)
    assert result.stdout.strip() == "http=503", "503 mit Notfallmodus ist nie 'needed=false'"
