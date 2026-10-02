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

import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
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


def _has_gnu_tar() -> bool:
    try:
        return "GNU tar" in subprocess.run(["tar", "--version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return False


# Der Hilfscontainer entpackt mit GNU tar (Debian); die Pruefungen gegen boesartige Namen beschreiben dessen Verhalten.
needs_gnu_tar = pytest.mark.skipif(not _has_gnu_tar(), reason="braucht GNU tar (wie im Hilfscontainer)")

OLD, NEW, BAD, MID, OLDER, LOOP = "sha256:old", "sha256:new", "sha256:bad", "sha256:mid", "sha256:older", "sha256:loop"
VOL = "deploy_lattice_data"
OLD_C, NEW_C = "deploy-lattice-1", "deploy-nodvard-deck-1"

# `docker`-Attrappe. Zustand: {"images": {ref: id}, "containers": {name: {"image", "running", ...}}}.
DOCKER_STUB = r'''#!__PYTHON__
import json, os, signal, sys

STATE = os.environ["STUB_STATE"]
with open(STATE + ".calls", "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]).replace("\n", "\\n") + "\n")
try:  # die Sperre von backup.sh/restore.sh (Deskriptor 9) darf Docker nicht erreichen
    os.fstat(9)
except OSError:
    pass
else:
    with open(STATE + ".fd9", "a", encoding="utf-8") as fh:
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


def is_check(a):
    """Die Vorabpruefung von backup.sh / restore.sh: liegt die Marke eines unterbrochenen Austauschs?"""
    script = a[-1] if a else ""
    return "-c" in a and ".restore-austausch" in script and "tar xzf -" not in script and ".restore-alt" not in script


def swap_path(volume):
    """PATH fuer das echte Austausch-Skript. STUB_SWAP_FAILS=alt|neu (1 = neu): `mv` scheitert beim Beiseiteschieben der alten
    bzw. beim Einsetzen der neuen Daten, nachdem es den ersten Eintrag schon verschoben hat -- wie ein echter Abbruch mittendrin."""
    import shutil

    where = env("STUB_SWAP_FAILS")
    if where in (None, ""):
        return os.environ["PATH"]
    target = ".restore-alt/" if where == "alt" else "."
    shim_dir = os.path.join(volume + ".bin")
    os.makedirs(shim_dir, exist_ok=True)
    real = shutil.which("mv")
    with open(os.path.join(shim_dir, "mv"), "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            'if [ "$1" = "-t" ] && [ "$2" = "%s" ]; then\n'
            '  dir="$2"; shift 2\n'
            '  "%s" -t "$dir" "$1"\n'
            '  echo "mv: cannot move: Input/output error" >&2\n'
            "  exit 1\n"
            "fi\n"
            'exec "%s" "$@"\n' % (target, real, real)
        )
    os.chmod(os.path.join(shim_dir, "mv"), 0o755)
    return shim_dir + os.pathsep + os.environ["PATH"]


def helper_container(a):
    """`compose run` fuer die Hilfscontainer von backup.sh / restore.sh (tar, sh, python). Unbekannte Formen: Exit 0."""
    import hashlib, io, stat, subprocess, tarfile, time

    if "nodvard-deck" not in a:
        return
    entry = a[a.index("--entrypoint") + 1] if "--entrypoint" in a else None
    binds = [a[i + 1].split(":") for i, x in enumerate(a) if x == "-v"]
    cmd = a[a.index("nodvard-deck") + 1:]
    # Der Container laeuft als Benutzer 1000: einen root-eigenen Ordner (STUB_ROOT_OWNED_DIR) darf er weder lesen noch beschreiben.
    for src, *_ in binds:
        if env("STUB_ROOT_OWNED_DIR") and src == env("STUB_ROOT_OWNED_DIR"):
            sys.stderr.write("tar: /backup: Cannot open: Permission denied\n")
            sys.exit(2)
    # Compose meldet auf STDERR; auf stdout steht nur, was der Container ausgibt.
    sys.stderr.write(" Container deploy-nodvard-deck-run-1  Creating\n Container deploy-nodvard-deck-run-1  Started\n")
    if entry == "tar" and cmd[:2] == ["czf", "-"]:
        if "-T" not in a:  # ohne -T haengt Compose ein TTY an und verfaelscht die Binaerdaten
            sys.stderr.write("the input device is not a TTY\n")
            sys.exit(1)
        if env("STUB_VOLUME"):  # das echte tar gegen den Ersatz-Ordner, mit genau den Optionen des Skripts
            sys.stdout.flush()
            done = subprocess.run(["tar", *[x.replace("/app/data", env("STUB_VOLUME")) for x in cmd]], stdout=sys.stdout.buffer)
            sys.exit(done.returncode)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            if env("STUB_TAR_TINY") != "1":
                for name, payload in (("lattice.db", os.urandom(8192)), ("jwt_secret.key", os.urandom(64))):
                    info = tarfile.TarInfo("./" + name)
                    info.size = len(payload)
                    tf.addfile(info, io.BytesIO(payload))
        data = buf.getvalue()
        out = sys.stdout.buffer
        if env("STUB_TAR_EMPTY") == "1":
            sys.exit(0)
        if env("STUB_TAR_NOISE") == "1":  # Fremdausgabe vor den tar-Daten
            out.write(b"Container deploy-nodvard-deck-run-1 Creating\n")
        if env("STUB_TAR_TRUNCATED") == "1":  # abgeschnitten, aber Exit 0
            data = data[: len(data) * 6 // 10]
        if env("STUB_TAR_FAILS_HALFWAY") == "1":  # halbe Ausgabe, dann Fehler (volle Karte o. ae.)
            out.write(data[: len(data) // 2])
            out.flush()
            sys.stderr.write("tar: write error\n")
            sys.exit(2)
        out.write(data)
        out.flush()
        sys.exit(0)
    if entry == "sh" and cmd[:1] == ["-c"] and "tar xzf -" in cmd[1]:
        # Schritt 1 von restore.sh: entpacken. Mit STUB_VOLUME=<Ordner> laeuft das echte Skript gegen diesen Ordner
        # (statt /app/data); ohne bildet die Attrappe nur nach, dass die Datei ankommt.
        if "-T" not in a:
            sys.stderr.write("the input device is not a TTY\n")
            sys.exit(1)
        if env("STUB_HUP_ON_UNPACK") == "1":  # die SSH-Verbindung reisst mitten im Einspielen ab: SIGHUP an die ganze Gruppe
            os.kill(os.getppid(), signal.SIGHUP)
            os.kill(os.getpid(), signal.SIGHUP)
        if env("STUB_INT_ON_UNPACK") == "1":  # Ctrl-C mitten im Entpacken: die Shell und der Hilfsprozess sterben an SIGINT
            data = sys.stdin.buffer.read()
            if env("STUB_VOLUME"):  # das echte Entpack-Skript laeuft schon und bekommt nur noch einen halben Datenstrom
                proc = subprocess.Popen(["sh", "-c", cmd[1].replace("/app/data", env("STUB_VOLUME"))], stdin=subprocess.PIPE)
                proc.stdin.write(data[: len(data) // 2])
                proc.stdin.flush()
                os.kill(os.getppid(), signal.SIGINT)
                proc.stdin.close()
                proc.wait()
            else:
                os.kill(os.getppid(), signal.SIGINT)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(10)
        if env("STUB_UNPACK_GATE"):  # wartet mitten im Entpacken, bis der Test weitermachen laesst (z. B. nach dem Auflegen)
            open(env("STUB_UNPACK_GATE") + ".started", "w").close()
            for _ in range(200):
                if os.path.exists(env("STUB_UNPACK_GATE") + ".go"):
                    break
                time.sleep(0.05)
        data = sys.stdin.buffer.read()
        with open(STATE + ".restored", "wb") as fh:
            fh.write(data)
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                names = tf.getnames()
        except Exception:
            sys.stderr.write("tar: Error is not recoverable: exiting now\n")
            sys.exit(2)
        volume = env("STUB_VOLUME")
        if env("STUB_RESTORE_FAILS") == "1" and not volume:
            sys.exit(2)
        with open(STATE + ".restored.names", "w", encoding="utf-8") as fh:
            fh.write("\n".join(names))
        if volume:
            if env("STUB_RESTORE_FAILS") == "1":  # der Datenstrom bricht mitten ab
                data = data[: len(data) // 2]
            done = subprocess.run(["sh", "-c", cmd[1].replace("/app/data", volume)], input=data)
            sys.exit(done.returncode)
        sys.exit(0)
    if entry == "sh" and is_check(cmd):
        # Vorabpruefung (Marke eines unterbrochenen Austauschs): mit STUB_VOLUME das echte Skript, sonst "keine Marke".
        if "-T" not in a:
            sys.stderr.write("the input device is not a TTY\n")
            sys.exit(1)
        if env("STUB_CHECK_FAILS") == "1":
            sys.stderr.write("Error response from daemon: something went wrong\n")
            sys.exit(1)
        if env("STUB_VOLUME"):
            sys.exit(subprocess.run(["sh", "-c", cmd[1].replace("/app/data", env("STUB_VOLUME"))], stdin=subprocess.DEVNULL).returncode)
        sys.exit(0)
    if entry == "sh" and cmd[:1] == ["-c"] and ".restore-alt" in cmd[1]:
        # Schritt 2 von restore.sh: austauschen (nur Umbenennungen).
        if "-T" not in a:
            sys.stderr.write("the input device is not a TTY\n")
            sys.exit(1)
        if env("STUB_SIGNAL_ON_SWAP"):  # HUP / INT / TERM waehrend des Austauschs, an die Shell und an diesen Prozess (wie vom Terminal)
            for pid in (os.getppid(), os.getpid()):
                os.kill(pid, getattr(signal, "SIG" + env("STUB_SIGNAL_ON_SWAP")))
            time.sleep(0.2)
        volume = env("STUB_VOLUME")
        if volume:  # das echte Skript; mit STUB_SWAP_FAILS scheitert dabei `mv` mittendrin
            done = subprocess.run(["sh", "-c", cmd[1].replace("/app/data", volume)], stdin=subprocess.DEVNULL, env={**os.environ, "PATH": swap_path(volume)})
            sys.exit(done.returncode)
        if env("STUB_SWAP_FAILS"):
            sys.stderr.write("mv: cannot move: Input/output error\n")
            sys.exit(2)
        sys.exit(0)
    if entry == "python" and "restore-backup" in cmd:
        # Haelt fest, WAS der Container unter /backup zu sehen bekommt (Ordner und Datei, mit Rechten).
        info = []
        for src, dst, *_ in binds:
            files = {}
            for name in os.listdir(src):
                path = os.path.join(src, name)
                with open(path, "rb") as fh:
                    sha = hashlib.sha256(fh.read()).hexdigest()
                files[name] = {"mode": stat.S_IMODE(os.stat(path).st_mode), "uid": os.stat(path).st_uid, "sha": sha}
            info.append({"src": src, "dst": dst, "dir_mode": stat.S_IMODE(os.stat(src).st_mode), "dir_uid": os.stat(src).st_uid, "files": files, "arg": cmd[-1]})
        with open(STATE + ".ndbak", "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        sys.exit(1 if env("STUB_NDBAK_FAILS") == "1" else 0)


cmd = args[0] if args else ""
rest = args[1:]
if env("STUB_DOCKER_NO_ACCESS") == "1":  # kein Zugriff auf den Docker-Dienst (nicht in der Gruppe, ohne sudo): jeder Befehl scheitert so
    sys.stderr.write("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock\n")
    sys.exit(1)
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
    if fmt and ".Created" in fmt:
        out(env("STUB_CREATED", "2026-10-02T03:00:00.123456789Z"))
    elif fmt and "Mounts" in fmt:
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
    if rest and rest[0] in ("stop", "start", "restart"):
        c = st["containers"].get(NEW_C)
        if c is None:
            sys.stderr.write("Error: no container found for service\n")
            sys.exit(1 if rest[0] != "stop" else 0)
        c["running"] = rest[0] != "stop"
        save()
    elif rest and rest[0] == "run":
        if env("STUB_RUN_FAILS") == "1" and not is_check(rest):
            sys.exit(1)
        helper_container(rest[1:])
        sys.exit(0)
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
elif cmd == "cp":
    # `docker cp <container>:.../.boot/state.json -`: tar-Strom, auch bei gestopptem Container (Volume). STUB_BOOTSTATE
    # (JSON) ist der Stand im Volume, so wie der Dienst ihn schreibt; ohne Angabe gestartet ohne Migration, leer = fehlt.
    import io, tarfile
    raw = env("STUB_BOOTSTATE", '{"started_ok": true}')
    if rest[0].split(":", 1)[0] not in st["containers"] or not raw:
        sys.stderr.write("Error: Could not find the file\n")
        sys.exit(1)
    data = (json.dumps(json.loads(raw), indent=1, sort_keys=True) + "\n").encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("state.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    sys.stdout.buffer.write(buf.getvalue())
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
    # STUB_CRASH_AFTER_HEALTH=1: der neue Container stuerzt direkt nach seinem ersten Health-Ok ab.
    if os.environ.get("STUB_CRASH_AFTER_HEALTH") == "1" and name == "deploy-nodvard-deck-1":
        st["containers"][name]["running"] = False
        st["containers"][name]["status"] = "exited"
        with open(os.environ["STUB_STATE"], "w", encoding="utf-8") as fh:
            json.dump(st, fh)
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
        return {**base, "PATH": f"{self.bin}{os.pathsep}{base['PATH']}", "STUB_STATE": str(self.state_file), "PI_SWITCH_LOG_DIR": str(self.tmp / "logs"), "RESTORE_LOG_DIR": str(self.tmp / "logs"), **extra}

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
    # Exit 4 = richtiges Volume, laeuft aber nicht (deploy_pi.sh trennt das vom falschen Volume).
    assert looping.returncode == 4 and "laeuft nicht" in looping.stderr and "restarting" in looping.stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: {"image": NEW, "running": True, "status": "paused", "volume": VOL}})
    assert "paused" in machine.switch("verify").stderr
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: {"image": NEW, "running": False, "status": "exited", "volume": VOL}})
    assert machine.switch("verify").returncode == 4
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: {"image": NEW, "running": False, "status": "exited", "volume": "foo_lattice_data"}})
    wrong_volume = machine.switch("verify")
    assert wrong_volume.returncode == 1 and "foo_lattice_data" in wrong_volume.stderr
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


def _bootstate(at: str, *, started_ok: bool = True, **extra: object) -> str:
    # Container erstellt um 2026-10-02T03:00:00 (STUB_CREATED-Vorgabe); eine Migration danach stammt von ihm.
    state = {"last_migration": {"at": at, "state": "ok", "from_heads": ["a1"], "to_heads": ["b2"]}, "started_ok": started_ok, "started_at": "2026-10-02T03:00:05Z"}
    return json.dumps({**state, **extra})


MIGRATED_NOW = {"STUB_BOOTSTATE": _bootstate("2026-10-02T03:00:02Z")}


@needs_bash
def test_deploy_pi_does_not_roll_back_once_the_new_version_has_migrated_and_started(machine, deploy_run):
    # Das alte Image wuerde die umgebauten Daten ablehnen: ein Rollback endete nur auf der Notseite.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_EMPTY_DATA="1", **MIGRATED_NOW)
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "KEIN automatischer Rollback" in result.stderr and "2026-10-02T03:00:02Z" in result.stderr
    assert "Datencheck" in result.stderr
    assert "start deploy-lattice-1" not in machine.calls and "Rollback ausgefuehrt" not in result.stderr
    assert machine.containers[NEW_C]["running"] is True and OLD_C in machine.containers, "nichts aufgeraeumt, nichts zurueckgeschaltet"


@needs_bash
@pytest.mark.parametrize(
    ("label", "extra"),
    [
        ("no-migration", {}),
        ("migration-older-than-the-container", {"STUB_BOOTSTATE": _bootstate("2026-09-01T10:00:00Z")}),
        ("never-started", {"STUB_BOOTSTATE": _bootstate("2026-10-02T03:00:02Z", started_ok=False)}),
        # Nur das oberste started_ok zaehlt, nicht das in pre_restore.
        ("only-nested-started-ok", {"STUB_BOOTSTATE": _bootstate("2026-10-02T03:00:02Z", started_ok=False, pre_restore={"id": "x", "started_ok": True})}),
    ],
)
def test_deploy_pi_still_rolls_back_when_the_old_version_can_take_the_data(machine, deploy_run, label, extra):
    # Keine Migration durch den neuen Container, oder er ist nie gestartet (dann setzt das alte Image die Kopie selbst zurueck).
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_EMPTY_DATA="1", **extra)
    assert result.returncode != 0 and "DEPLOY-OK" not in result.stdout, label
    assert "Rollback ausgefuehrt" in result.stderr and "KEIN automatischer Rollback" not in result.stderr, label
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}, label


@needs_bash
@pytest.mark.parametrize(
    ("label", "extra"),
    [
        ("state-unreadable", {"STUB_BOOTSTATE": ""}),
        ("connection-dropped", {"STUB_SSH_FAIL": "/app/data/.boot/state.json -"}),
    ],
)
def test_deploy_pi_unknown_migration_state_fails_without_an_automatic_rollback(machine, deploy_run, label, extra):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_EMPTY_DATA="1", **extra)
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout, label
    assert "Zustand unklar" in result.stderr and "KEIN automatischer Rollback" in result.stderr, label
    assert "start deploy-lattice-1" not in machine.calls and "Rollback ausgefuehrt" not in result.stderr, label
    assert machine.containers[NEW_C]["running"] is True and OLD_C in machine.containers, label


@needs_bash
def test_deploy_pi_unknown_migration_state_does_not_block_a_good_deploy(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_BOOTSTATE="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEPLOY-OK" in result.stdout


@needs_bash
def test_deploy_pi_crash_after_a_migration_is_not_rolled_back(machine, deploy_run):
    # Gesund, Migration durch, dann abgestuerzt: der Rollback braechte nur die Notseite des alten Images.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_CRASH_AFTER_HEALTH="1", **MIGRATED_NOW)
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "nicht mehr" in result.stderr and "KEIN automatischer Rollback" in result.stderr and "2026-10-02T03:00:02Z" in result.stderr
    assert "start deploy-lattice-1" not in machine.calls and "Rollback ausgefuehrt" not in result.stderr
    assert machine.containers[NEW_C]["running"] is False and OLD_C in machine.containers


@needs_bash
def test_deploy_pi_crash_without_a_migration_is_rolled_back(machine, deploy_run):
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_CRASH_AFTER_HEALTH="1")
    assert result.returncode != 0 and "Rollback ausgefuehrt" in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
def test_deploy_pi_wrong_volume_is_rolled_back_even_after_a_migration(machine, deploy_run):
    # Falsches Volume: die alten Daten sind unberuehrt, das alte Image kann sie weiter lesen.
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_COMPOSE_VOLUME="foo_lattice_data", **MIGRATED_NOW)
    assert result.returncode != 0 and "Rollback ausgefuehrt" in result.stderr
    assert machine.containers == {OLD_C: {"image": OLD, "running": True}}


@needs_bash
@pytest.mark.parametrize("step", ["pi_switch.sh verify", "pi_switch.sh bootstrap"])
def test_deploy_pi_dropped_connection_while_checking_does_not_roll_back(machine, deploy_run, step):
    # ssh 255 heisst "Zustand unbekannt", nicht "neuer Stand kaputt".
    _deploy_state(machine, deploy_run)
    result = deploy_run(STUB_SSH_FAIL=step)
    assert result.returncode != 0 and "DEPLOY-FAIL" in result.stderr and "DEPLOY-OK" not in result.stdout
    assert "abgerissen" in result.stderr and "KEIN automatischer Rollback" in result.stderr
    assert "start deploy-lattice-1" not in machine.calls and "ROLLBACK" not in result.stderr
    assert machine.containers[NEW_C]["running"] is True


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
def _write_archive(path: Path, files: dict[str, bytes] | None = None) -> bytes:
    """Ein echtes tar.gz wie von backup.sh: restore.sh prueft die Datei vorab mit `tar tzf`."""
    if files is None:
        files = {"lattice.db": os.urandom(4096), "jwt_secret.key": b"geheim"}
    with tarfile.open(path, "w:gz") as tf:
        for name, payload in files.items():
            info = tarfile.TarInfo("./" + name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return path.read_bytes()


def _can_chown() -> bool:
    """root mit CAP_CHOWN (Probe-chown): nur dann kann ein Test Dateien einem anderen Besitzer geben."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    import tempfile

    fd, probe = tempfile.mkstemp()
    try:
        os.close(fd)
        os.chown(probe, 12345, 23456)
    except OSError:
        return False
    finally:
        os.unlink(probe)
    return True


CAN_CHOWN = _can_chown()


def needs_root(func):
    """Marker `needs_root` (die CI waehlt ihn mit `-m needs_root` fuer einen eigenen Lauf als root aus) und Skip ohne root."""
    skip = pytest.mark.skipif(not CAN_CHOWN, reason="braucht root mit CAP_CHOWN (Dateien eines anderen Besitzers)")
    return pytest.mark.needs_root(skip(func))


def _script_copy(tmp_path: Path, name: str) -> Path:
    """Die Skripte fuer Tests, die als root laufen, aus einer Kopie in einem Temp-Ordner ausfuehren: so haengt der Lauf nicht
    an den Rechten des Checkouts (auf den GitHub-Runnern liegt er im Home-Verzeichnis des Runners, Modus 0750)."""
    folder = tmp_path / "skript-kopie"
    folder.mkdir(exist_ok=True)
    target = folder / name
    shutil.copy2(ROOT / "deploy" / name, target)
    return target


@pytest.fixture
def backup_env(machine, tmp_path):
    def run(script: str, *args: str, stdin: str = "", from_copy: bool = True, **env: str) -> subprocess.CompletedProcess:
        path = _script_copy(tmp_path, script) if from_copy else ROOT / "deploy" / script
        return subprocess.run(
            ["bash", str(path), *args], env=machine.env(**env), input=stdin,
            capture_output=True, text=True, timeout=60, check=False,
        )

    return run


def _wrap(machine: Machine, name: str, body: str) -> None:
    """Attrappe fuer ein Werkzeug im PATH: `body` (Bash) laeuft zuerst, sonst wird das echte Programm aufgerufen."""
    import shlex

    real = shutil.which(name)
    assert real, name
    _executable(machine.bin / name, f'#!/usr/bin/env bash\n{body}\nexec {shlex.quote(real)} "$@"\n')


def _fake_uid(machine: Machine) -> None:
    """`id -u` meldet STUB_ID_U (damit sich die Zweige root / 1000 / andere ohne echten Benutzerwechsel pruefen lassen)."""
    _wrap(machine, "id", 'if [ "${1:-}" = "-u" ] && [ -n "${STUB_ID_U:-}" ]; then echo "$STUB_ID_U"; exit 0; fi')


def _fail_for_partial_files(machine: Machine, tool: str, text: str) -> None:
    """`rm`/`mv` scheitern fuer Zwischendateien (`*.partial*`), alles andere laeuft wie sonst."""
    _wrap(machine, tool, f'case "$*" in *.partial*) echo "{tool}: {text}" >&2; exit 1;; esac')


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_and_restore_stop_a_still_running_old_container_and_start_it_again(machine, backup_env, tmp_path, script):
    machine.set(images={"nodvard-deck:latest": OLD}, containers=_old_running())
    archive = tmp_path / "sicherung.tar.gz"
    _write_archive(archive)
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = machine.calls
    assert calls.index("stop -t 30 deploy-lattice-1") < calls.index(_run_call(machine)) < calls.index("start deploy-lattice-1")
    assert machine.containers[OLD_C]["running"] is True
    assert "compose -p deploy start nodvard-deck" not in calls


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_backup_and_restore_keep_their_old_behaviour_when_only_the_new_service_runs(machine, backup_env, tmp_path, script):
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})
    archive = tmp_path / "sicherung.tar.gz"
    _write_archive(archive)
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = machine.calls
    assert not any(c.startswith("stop") for c in calls)
    assert calls.index("compose -p deploy stop nodvard-deck") < calls.index(_run_call(machine)) < calls.index("compose -p deploy start nodvard-deck")


@needs_bash
def test_restore_that_fails_still_starts_the_old_container_again(machine, backup_env, tmp_path):
    machine.set(images={"nodvard-deck:latest": OLD}, containers=_old_running())
    archive = tmp_path / "sicherung.tar.gz"
    _write_archive(archive)
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
    _write_archive(archive)
    args = (str(tmp_path),) if script == "backup.sh" else (str(archive),)
    assert backup_env(script, *args, stdin="y\n").returncode == 0
    runs = [c for c in machine.calls if c.startswith("compose -p deploy run")]
    assert runs and all("--user 1000:1000" in c for c in runs), runs


# ---------------------------------------------------------------------------
# backup.sh: die Datei legt die Host-Shell an (tar -> stdout), auch als root
# ---------------------------------------------------------------------------
def _up(machine: Machine) -> None:
    machine.set(images={"nodvard-deck:latest": NEW}, containers={NEW_C: _running(NEW, volume=VOL)})


def _backups_in(directory: Path) -> list[Path]:
    return sorted(directory.iterdir()) if directory.exists() else []


def _is_check_call(call: str) -> bool:
    """Die Vorabpruefung auf die Marke eines unterbrochenen Austauschs (aendert nichts)."""
    return call.startswith("compose -p deploy run") and ".restore-austausch" in call and "exit 3" in call and "tar xzf -" not in call


def _run_call(machine: Machine) -> str:
    """Der erste Hilfscontainer, der Daten bewegt (tar bzw. Entpacken), ohne die Vorabpruefung."""
    return next(c for c in machine.calls if c.startswith("compose -p deploy run") and not _is_check_call(c))


@needs_bash
def test_backup_streams_tar_over_stdout_and_the_host_shell_creates_the_file(machine, backup_env, tmp_path):
    _up(machine)
    out = tmp_path / "sicherungen"
    result = backup_env("backup.sh", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    files = _backups_in(out)
    assert len(files) == 1 and files[0].name.startswith("nodvard-deck-backup-") and files[0].name.endswith(".tar.gz"), files
    assert f"Fertig: {files[0]}" in result.stdout
    with tarfile.open(files[0], "r:gz") as tf:
        assert {"./lattice.db", "./jwt_secret.key"} <= set(tf.getnames())
    call = _run_call(machine)
    # Container schreibt nach stdout (`czf -`, ohne TTY), der Zielordner wird NICHT in den Container eingebunden.
    assert " -T " in call and "--entrypoint tar" in call and "--user 1000:1000" in call
    assert "czf - --exclude=./.restore-neu --exclude=./.restore-alt* --exclude=./.restore-austausch* -C /app/data ." in call
    assert " -v " not in call and "/backup" not in call, call
    # Dienst: erst stoppen, dann sichern, dann wieder starten.
    calls = machine.calls
    assert calls.index("compose -p deploy stop nodvard-deck") < calls.index(call) < calls.index("compose -p deploy start nodvard-deck")


@needs_bash
def test_backup_started_as_root_works_when_the_target_directory_is_not_writable_for_user_1000(machine, backup_env, tmp_path):
    # `sudo ./backup.sh ~/nodvard-deck-backups`: der Zielordner gehoert root, der Container (Benutzer 1000) darf dort nicht
    # schreiben. Frueher band das Skript den Ordner in den Container ein und scheiterte; jetzt schreibt die Host-Shell selbst.
    _up(machine)
    out = tmp_path / "root-ordner"
    out.mkdir()
    result = backup_env("backup.sh", str(out), STUB_ROOT_OWNED_DIR=str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_backups_in(out)) == 1


@needs_bash
def test_backup_file_is_private_even_when_the_callers_umask_is_open(machine, tmp_path):
    # Die Sicherung enthaelt den Master-Key: 0600, egal welche umask der Aufrufer hat.
    _up(machine)
    out = tmp_path / "offen"
    result = subprocess.run(
        ["bash", "-c", 'umask 000; exec bash "$0" "$1"', str(_script_copy(tmp_path, "backup.sh")), str(out)],
        env=machine.env(), capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (archive,) = _backups_in(out)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert not list(out.glob("*.partial"))


@needs_bash
@pytest.mark.parametrize(
    "fault",
    ["STUB_TAR_FAILS_HALFWAY", "STUB_TAR_EMPTY", "STUB_TAR_TINY", "STUB_TAR_NOISE", "STUB_TAR_TRUNCATED", "STUB_RUN_FAILS"],
)
def test_backup_that_goes_wrong_leaves_no_file_behind_and_still_starts_the_service(machine, backup_env, tmp_path, fault):
    # halbe Ausgabe + Fehler / nichts / winziges Archiv / Fremdausgabe auf stdout / abgeschnitten / Docker-Fehler
    _up(machine)
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out), **{fault: "1"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FEHLER" in result.stderr
    assert "Fertig" not in result.stdout
    assert _backups_in(out) == [], "weder eine fertige noch eine halbe Datei"
    assert "compose -p deploy start nodvard-deck" in machine.calls, "der Dienst laeuft danach wieder"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_backup_that_goes_wrong_does_not_touch_an_older_backup_in_the_same_folder(machine, backup_env, tmp_path):
    _up(machine)
    out = tmp_path / "ziel"
    out.mkdir()
    older = out / "nodvard-deck-backup-20200101-000000.tar.gz"
    older.write_bytes(b"alt")
    assert backup_env("backup.sh", str(out), STUB_TAR_FAILS_HALFWAY="1").returncode != 0
    assert _backups_in(out) == [older] and older.read_bytes() == b"alt"


@needs_bash
def test_backup_into_a_folder_that_cannot_be_written_fails_before_the_service_is_stopped(machine, tmp_path):
    # Die Zwischendatei laesst sich nicht anlegen (als root geht ein Rechtetrick nicht, deshalb scheitert `mktemp` per Attrappe):
    # das muss auffallen, bevor der Dienst angehalten wird.
    _up(machine)
    out = tmp_path / "ziel"
    _executable(machine.bin / "mktemp", '#!/usr/bin/env bash\necho "mktemp: failed to create file via template: Permission denied" >&2\nexit 1\n')
    result = subprocess.run(["bash", str(_script_copy(tmp_path, "backup.sh")), str(out)], env=machine.env(), capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0 and "laesst sich nicht schreiben" in result.stderr and "nichts angehalten" in result.stderr
    assert not any(c.startswith(("stop", "compose")) for c in machine.calls), machine.calls
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
@pytest.mark.parametrize("fault", ["tar-scheitert", "umbenennen-scheitert"])
def test_backup_whose_cleanup_fails_still_starts_the_service(machine, backup_env, tmp_path, fault):
    # Der Zielordner ist nur noch lesbar oder veraltet (ESTALE): weder das Umbenennen noch das Loeschen der halben Datei klappt.
    # Frueher brach der EXIT-Trap an diesem Fehler ab, und der Dienst blieb gestoppt.
    _up(machine)
    _fail_for_partial_files(machine, "rm", "cannot remove: Read-only file system")
    extra = {}
    if fault == "tar-scheitert":
        extra["STUB_TAR_FAILS_HALFWAY"] = "1"
    else:
        _fail_for_partial_files(machine, "mv", "cannot move: Stale file handle")
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out), **extra)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "bitte von Hand entfernen" in result.stderr, "ein Hinweis statt eines stillen Abbruchs"
    assert "compose -p deploy start nodvard-deck" in machine.calls
    assert machine.containers[NEW_C]["running"] is True
    assert len(list(out.glob("*.partial.*"))) == 1, "die halbe Datei bleibt liegen (rm scheiterte), der Dienst laeuft trotzdem"
    assert not list(out.glob("*.tar.gz"))


@needs_bash
@pytest.mark.parametrize("target_exists", [True, False])
def test_backup_does_not_follow_links_planted_in_the_target_folder(machine, backup_env, tmp_path, target_exists):
    # Wer in den Zielordner schreiben darf, legt Links unter den frueher vorhersehbaren Namen ab: root darf dadurch keine
    # fremde Datei ueberschreiben. Der Zwischenname ist jetzt zufaellig, und umbenannt wird mit `mv -fT` (ersetzt den Link).
    _up(machine)
    _executable(machine.bin / "date", '#!/usr/bin/env bash\necho 20260101-000000\n')
    out = tmp_path / "ziel"
    out.mkdir()
    name = "nodvard-deck-backup-20260101-000000.tar.gz"
    partial_victim, final_victim = tmp_path / "fremd-1.txt", tmp_path / "fremd-2.txt"
    partial_victim.write_bytes(b"eins")
    if target_exists:
        final_victim.write_bytes(b"zwei")
    (out / f"{name}.partial").symlink_to(partial_victim)
    (out / name).symlink_to(final_victim)
    result = backup_env("backup.sh", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    assert partial_victim.read_bytes() == b"eins"
    assert (final_victim.read_bytes() == b"zwei") if target_exists else not final_victim.exists()
    assert (out / f"{name}.partial").is_symlink(), "der abgelegte Zwischen-Link wurde nicht angefasst"
    assert not (out / name).is_symlink() and tarfile.is_tarfile(out / name), "unter dem Endnamen liegt jetzt die echte Sicherung"
    assert sorted(path.name for path in out.iterdir()) == [name, f"{name}.partial"]


@needs_bash
def test_backup_with_a_folder_in_the_place_of_the_final_name_fails_cleanly(machine, backup_env, tmp_path):
    _up(machine)
    _executable(machine.bin / "date", '#!/usr/bin/env bash\necho 20260101-000000\n')
    out = tmp_path / "ziel"
    name = "nodvard-deck-backup-20260101-000000.tar.gz"
    (out / name).mkdir(parents=True)
    (out / name / "drin.txt").write_bytes(b"x")
    result = backup_env("backup.sh", str(out))
    assert result.returncode != 0 and "Fertig" not in result.stdout
    assert sorted(path.name for path in out.iterdir()) == [name] and (out / name / "drin.txt").exists(), "keine halbe Datei, nichts verschoben"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_backup_without_the_image_stops_before_anything_is_changed(machine, backup_env, tmp_path):
    # Fehlt das Image, wuerde `compose run` es erst bauen -- bei gestopptem Dienst, und mit dem Bauprotokoll auf stdout.
    machine.set(images={}, containers={NEW_C: _running(NEW, volume=VOL)})
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out))
    assert result.returncode != 0 and "nodvard-deck:latest" in result.stderr and "nichts angehalten" in result.stderr
    assert "gibt es hier nicht" in result.stderr and "Docker antwortet nicht" not in result.stderr
    assert machine.calls == ["image inspect nodvard-deck:latest", "info"], "nur Pruefungen: nichts gestoppt, nichts gestartet, nichts gebaut"
    assert machine.containers[NEW_C]["running"] is True
    assert _backups_in(out) == []


def test_the_restore_log_next_to_the_script_is_not_committed():
    git = shutil.which("git")
    if git is None:
        pytest.skip("git fehlt")
    done = subprocess.run([git, "check-ignore", "-q", "deploy/restore.log"], cwd=ROOT, capture_output=True, check=False)
    if done.returncode == 128:
        pytest.skip("kein Git-Arbeitsordner")
    assert done.returncode == 0, "deploy/restore.log (Pfade, Benutzernamen) gehoert in .gitignore"


@needs_bash
def test_backup_writes_the_file_to_disk_before_it_gets_its_final_name(machine, backup_env, tmp_path):
    import shlex

    _up(machine)
    trace = tmp_path / "ablauf"
    for tool in ("sync", "mv"):
        _wrap(machine, tool, f'echo "{tool} $*" >> {shlex.quote(str(trace))}')
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    steps = trace.read_text(encoding="utf-8").splitlines()
    syncs = [i for i, s in enumerate(steps) if s.startswith("sync ") and ".partial." in s]
    renames = [i for i, s in enumerate(steps) if s.startswith("mv -fT ") and ".partial." in s]
    assert len(syncs) == len(renames) == 1 and syncs[0] < renames[0], steps
    assert len(_backups_in(out)) == 1


@needs_bash
def test_backup_still_works_when_sync_is_not_available(machine, backup_env, tmp_path):
    # Ein fehlendes oder scheiterndes `sync` darf die Sicherung nicht verhindern (nur die Absicherung faellt weg).
    _up(machine)
    _executable(machine.bin / "sync", "#!/usr/bin/env bash\necho 'sync: unrecognized option' >&2\nexit 1\n")
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_backups_in(out)) == 1


@needs_bash
def test_backup_without_access_to_docker_says_so_instead_of_claiming_the_image_is_missing(machine, backup_env, tmp_path):
    # Ohne Docker-Zugriff (nicht in der Gruppe, ohne sudo) scheitert schon die Image-Pruefung; das darf nicht wie ein
    # fehlendes Image aussehen, sonst baut oder liefert jemand etwas aus, obwohl nur das Recht fehlt.
    _up(machine)
    _fake_uid(machine)
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out), STUB_DOCKER_NO_ACCESS="1", STUB_ID_U="1000")
    assert result.returncode != 0 and "Docker antwortet nicht" in result.stderr
    assert "permission denied" in result.stderr, "Dockers eigene Meldung steht dabei"
    assert "sudo" in result.stderr and "Gruppe docker" in result.stderr and "nichts angehalten" in result.stderr, "sagt, was zu tun ist"
    assert "gibt es hier nicht" not in result.stderr and "noch nicht gebaut" not in result.stderr
    assert not any(c.startswith(("stop", "compose")) for c in machine.calls), machine.calls
    assert machine.containers[NEW_C]["running"] is True
    assert _backups_in(out) == []


@needs_bash
def test_backup_as_root_without_an_answer_from_docker_does_not_suggest_sudo(machine, backup_env, tmp_path):
    # Wer schon mit sudo (oder als root) startet, dem fehlt kein Recht: dann laeuft der Docker-Dienst nicht.
    _up(machine)
    _fake_uid(machine)
    out = tmp_path / "ziel"
    result = backup_env("backup.sh", str(out), STUB_DOCKER_NO_ACCESS="1", STUB_ID_U="0")
    assert result.returncode != 0 and "Docker antwortet nicht" in result.stderr and "Docker-Dienst" in result.stderr
    assert "sudo" not in result.stderr and "Gruppe docker" not in result.stderr and "nichts angehalten" in result.stderr
    assert "gibt es hier nicht" not in result.stderr
    assert not any(c.startswith(("stop", "compose")) for c in machine.calls), machine.calls
    assert _backups_in(out) == []


@needs_bash
@pytest.mark.parametrize(
    ("dir_owner", "sudo_uid", "sudo_gid", "expected"),
    [
        ("12345:23456", "", "", "12345:23456"),  # root ohne sudo (Crontab): der Besitzer des Zielordners
        ("0:0", "", "", None),  # root-eigener Ordner: bleibt root
        ("12345:23456", "777", "888", "777:888"),  # mit sudo gewinnt der sudo-Aufrufer
        ("0:0", "0", "0", None),  # SUDO_UID=0 zaehlt nicht als Benutzer
    ],
)
def test_backup_started_as_root_gives_the_file_to_the_right_owner(machine, backup_env, tmp_path, dir_owner, sudo_uid, sudo_gid, expected):
    # `id` meldet root, `stat` den Ordnerbesitzer, `chown` schreibt nur auf, was es tun sollte: so geht es ohne echtes root.
    _up(machine)
    _fake_uid(machine)
    _wrap(machine, "stat", 'if [ "${1:-}" = "-c" ] && [ "${2:-}" = "%u:%g" ] && [ -n "${STUB_DIR_OWNER:-}" ]; then echo "$STUB_DIR_OWNER"; exit 0; fi')
    _executable(machine.bin / "chown", '#!/usr/bin/env bash\necho "$*" >> "$STUB_STATE.chown"\n')
    out = tmp_path / "vorhanden"
    out.mkdir()
    result = backup_env(
        "backup.sh", str(out), STUB_ID_U="0", STUB_DIR_OWNER=dir_owner, SUDO_UID=sudo_uid, SUDO_GID=sudo_gid,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    log = Path(str(machine.state_file) + ".chown")
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    calls = [c for c in calls if ".backup-restore.lock" not in c]  # die Sperrdatei gehoert (nach sudo) dem Aufrufer: eigener Test
    if expected is None:
        assert calls == []
    else:
        (call,) = calls
        assert call.startswith(f"-h {expected} {out}/nodvard-deck-backup-") and ".tar.gz.partial." in call, "die Zwischendatei, per -h (kein Link verfolgt)"


@needs_bash
@needs_root
def test_backup_started_as_root_without_sudo_gives_the_file_to_the_owner_of_the_folder(machine, backup_env, tmp_path):
    # Die dokumentierte root-Crontab: ohne SUDO_UID gehoerte die Datei root (0600), und die Kopie weg vom Pi per scp klappte nicht.
    _up(machine)
    out = tmp_path / "vorhanden"
    out.mkdir()
    os.chown(out, 12345, 23456)
    out.chmod(0o777)
    result = backup_env("backup.sh", str(out), from_copy=True, SUDO_UID="", SUDO_GID="")
    assert result.returncode == 0, result.stdout + result.stderr
    (archive,) = _backups_in(out)
    assert (archive.stat().st_uid, archive.stat().st_gid) == (12345, 23456)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    rooted = tmp_path / "root-ordner"
    rooted.mkdir()
    assert backup_env("backup.sh", str(rooted), from_copy=True, SUDO_UID="", SUDO_GID="").returncode == 0
    assert _backups_in(rooted)[0].stat().st_uid == 0, "gehoert der Ordner root, bleibt auch die Datei bei root"


@needs_bash
@needs_root
def test_backup_started_with_sudo_hands_the_file_and_a_new_folder_to_the_sudo_user(machine, backup_env, tmp_path):
    _up(machine)
    out = tmp_path / "neu"
    result = backup_env("backup.sh", str(out), from_copy=True, SUDO_UID="12345", SUDO_GID="23456")
    assert result.returncode == 0, result.stdout + result.stderr
    (archive,) = _backups_in(out)
    for path in (archive, out):
        assert (path.stat().st_uid, path.stat().st_gid) == (12345, 23456), path
    assert (tmp_path.stat().st_uid) != 12345, "nur der neu angelegte Zielordner, nicht der Elternordner"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    # Ein schon vorhandener Ordner bleibt, wie er ist; ohne SUDO_UID (oder mit 0) bleibt alles beim Aufrufer.
    again = tmp_path / "vorhanden"
    again.mkdir()
    assert backup_env("backup.sh", str(again), from_copy=True, SUDO_UID="12345", SUDO_GID="23456").returncode == 0
    assert again.stat().st_uid == os.geteuid() and _backups_in(again)[0].stat().st_uid == 12345
    plain = tmp_path / "ohne"
    assert backup_env("backup.sh", str(plain), from_copy=True, SUDO_UID="0").returncode == 0
    assert _backups_in(plain)[0].stat().st_uid == os.geteuid()


# ---------------------------------------------------------------------------
# restore.sh: die Datei kommt ueber stdin (gelesen von der Host-Shell)
# ---------------------------------------------------------------------------
@needs_bash
def test_restore_feeds_the_archive_over_stdin_so_file_and_folder_permissions_do_not_matter(machine, backup_env, tmp_path):
    _up(machine)
    folder = tmp_path / "root-ordner"
    folder.mkdir()
    archive = folder / "nodvard-deck-backup-20260101-000000.tar.gz"
    payload = _write_archive(archive)
    archive.chmod(0o600)
    # Der Container (1000) darf diesen Ordner weder lesen noch beschreiben: eingebunden wird er deshalb nie.
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_ROOT_OWNED_DIR=str(folder))
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(str(machine.state_file) + ".restored").read_bytes() == payload, "die Datei kam unveraendert ueber stdin an"
    assert "./lattice.db" in Path(str(machine.state_file) + ".restored.names").read_text(encoding="utf-8").splitlines()
    call = _run_call(machine)
    assert " -T " in call and "--entrypoint sh" in call and "tar xzf - -C /app/data" in call and "--user 1000:1000" in call
    assert " -v " not in call and "/backup" not in call and archive.name not in call, call
    calls = machine.calls
    assert calls.index("compose -p deploy stop nodvard-deck") < calls.index(call) < calls.index("compose -p deploy start nodvard-deck")


@needs_bash
def test_restore_with_a_file_name_that_would_break_a_shell_command(machine, backup_env, tmp_path):
    # Frueher stand der Dateiname im `sh -c`-Text im Container: Leerzeichen und Anfuehrungszeichen brachen das.
    _up(machine)
    archive = tmp_path / "meine sicherung 'alt'; echo.tar.gz"
    payload = _write_archive(archive)
    result = backup_env("restore.sh", str(archive), stdin="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(str(machine.state_file) + ".restored").read_bytes() == payload


@needs_bash
@pytest.mark.parametrize("kind", ["kein-gzip", "abgeschnitten", "kein-tar"])
def test_restore_refuses_a_broken_archive_before_anything_is_stopped_or_wiped(machine, backup_env, tmp_path, kind):
    _up(machine)
    archive = tmp_path / "kaputt.tar.gz"
    if kind == "kein-gzip":
        archive.write_bytes(b"x")
    elif kind == "abgeschnitten":
        data = _write_archive(archive)
        archive.write_bytes(data[: len(data) // 2])
    else:
        import gzip

        archive.write_bytes(gzip.compress(b"das ist kein tar, nur komprimierter Text" * 100))
    result = backup_env("restore.sh", str(archive), stdin="y\n")
    assert result.returncode != 0 and "kein lesbares tar.gz" in result.stderr and "nichts veraendert" in result.stderr
    assert machine.calls == [], "kein einziger Docker-Aufruf: nichts gestoppt, nichts geleert"


@needs_bash
def test_restore_that_fails_while_unpacking_says_so_and_still_starts_the_service(machine, backup_env, tmp_path):
    _up(machine)
    archive = tmp_path / "sicherung.tar.gz"
    _write_archive(archive)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_RESTORE_FAILS="1")
    assert result.returncode != 0 and "FEHLER" in result.stderr and "Das Einspielen ist fehlgeschlagen" in result.stderr
    assert "unveraendert" in result.stderr, "die bisherigen Daten sind beim Entpacken nicht angefasst worden"
    assert "Fertig." not in result.stdout
    assert "compose -p deploy start nodvard-deck" in machine.calls
    assert len(_run_calls(machine)) == 1, "der Austausch lief nicht"


OLD_DATA = {
    "lattice.db": b"alte datenbank",
    "master.key": b"alter schluessel",
    "backups/ui-sicherung.ndbak": b"u" * 100,
    "backups/vor-update/kopie.db": b"v" * 100,
    ".versteckt": b"punkt-datei",
    "name mit leerzeichen.txt": b"leerzeichen",
}
NEW_DATA = {"lattice.db": b"neue datenbank", "jwt_secret.key": b"neuer geheimer schluessel"}


def _volume(tmp_path: Path, files: dict[str, bytes] = OLD_DATA) -> Path:
    """Ein Ordner als Ersatz fuer /app/data (STUB_VOLUME): das echte Skript des Hilfscontainers laeuft gegen ihn."""
    folder = tmp_path / "volume"
    for name, payload in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return folder


def _tree(folder: Path) -> dict[str, bytes | None]:
    return {str(path.relative_to(folder)): (path.read_bytes() if path.is_file() else None) for path in sorted(folder.rglob("*"))}


def _files_only(files: dict[str, bytes]) -> dict[str, bytes | None]:
    """Wie `_tree` fuer Dateien in Unterordnern: die Ordner kommen als `None` dazu."""
    tree: dict[str, bytes | None] = dict(files)
    for name in files:
        for parent in Path(name).parents:
            if str(parent) != ".":
                tree[str(parent)] = None
    return dict(sorted(tree.items()))


def _run_calls(machine: Machine) -> list[str]:
    """Die Hilfscontainer, die Daten bewegen (Entpacken, Austausch), ohne die Vorabpruefung."""
    return [c for c in machine.calls if c.startswith("compose -p deploy run") and not _is_check_call(c)]


@needs_bash
def test_restore_unpacks_beside_the_old_data_and_then_swaps_them_in(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    # Ein halb entpackter Ordner eines frueheren, abgebrochenen Laufs (ohne Marke: die alten Daten sind unberuehrt)
    # wird ersetzt; er stammt nur aus einer Sicherungsdatei.
    (vol / ".restore-neu").mkdir()
    (vol / ".restore-neu" / "rest").write_bytes(b"x")
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Fertig." in result.stdout
    assert _tree(vol) == NEW_DATA, "nur noch die neuen Daten, keine Reste (auch keine Punkt-Dateien und .restore-*)"
    runs = _run_calls(machine)
    assert len(runs) == 2 and "tar xzf - -C /app/data/.restore-neu" in runs[0] and ".restore-alt" in runs[1] and "tar xzf" not in runs[1]
    calls = machine.calls
    assert calls.index("compose -p deploy stop nodvard-deck") < calls.index(runs[0]) < calls.index(runs[1]) < calls.index("compose -p deploy start nodvard-deck")
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_that_breaks_off_while_unpacking_leaves_the_old_data_untouched(machine, backup_env, tmp_path):
    # Der Datenstrom bricht mitten ab (voller Datentraeger, Lesefehler): frueher war das Volume dann schon geleert.
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, {"lattice.db": os.urandom(200_000), "jwt_secret.key": b"geheim"})
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_RESTORE_FAILS="1")
    assert result.returncode != 0 and "FEHLER" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before, "alte Daten vollstaendig da, kein .restore-neu zurueckgeblieben"
    assert len(_run_calls(machine)) == 1
    assert machine.containers[NEW_C]["running"] is True and "compose -p deploy start nodvard-deck" in machine.calls


@needs_bash
def test_restore_of_an_archive_without_files_does_not_wipe_anything(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "leer.tar.gz"
    _write_archive(archive, {})
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "enthaelt keine Dateien" in result.stderr
    assert _tree(vol) == before and len(_run_calls(machine)) == 1
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_whose_swap_fails_does_not_start_the_service_on_half_data_and_a_repeat_fixes_it(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SWAP_FAILS="1")
    assert result.returncode != 0 and "FEHLER" in result.stderr
    assert "wiederholen" in result.stderr and "NICHT gestartet" in result.stderr and str(archive) in result.stderr, "sagt, was zu tun ist"
    assert "Fertig." not in result.stdout
    assert "compose -p deploy start nodvard-deck" not in machine.calls
    assert machine.containers[NEW_C]["running"] is False, "der Dienst bleibt gestoppt"
    assert (vol / ".restore-alt").is_dir() and (vol / ".restore-neu").is_dir(), "halber Zustand wie nach einem echten Abbruch"
    # Wiederholen: raeumt die Reste weg und tauscht richtig aus.
    again = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _tree(vol) == NEW_DATA
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_keeps_the_old_container_stopped_too_when_the_swap_fails(machine, backup_env, tmp_path):
    machine.set(images={"nodvard-deck:latest": OLD}, containers=_old_running())
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_SWAP_FAILS="1")
    assert result.returncode != 0 and "NICHT gestartet" in result.stderr
    assert machine.containers[OLD_C]["running"] is False and f"start {OLD_C}" not in machine.calls


@needs_bash
def test_restore_continues_when_the_ssh_connection_drops_while_unpacking(machine, backup_env, tmp_path):
    # SIGHUP an die Shell (Verbindungsabbruch) mitten im Entpacken: das Einspielen muss trotzdem fertig werden.
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_HUP_ON_UNPACK="1")
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert _tree(vol) == NEW_DATA and len(_run_calls(machine)) == 2
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
@pytest.mark.parametrize("sig", ["HUP", "INT", "TERM"])
def test_restore_swap_is_not_interrupted_by_hangup_ctrl_c_or_terminate(machine, backup_env, tmp_path, sig):
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SIGNAL_ON_SWAP=sig)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert _tree(vol) == NEW_DATA and "Fertig." in result.stdout
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_interrupted_with_ctrl_c_while_unpacking_leaves_the_old_data_and_starts_the_service(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    # Die Attrappe startet das echte Entpack-Skript, gibt ihm einen halben Datenstrom und schickt erst dann Ctrl-C.
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_INT_ON_UNPACK="1")
    assert result.returncode != 0 and "Fertig." not in result.stdout
    assert _tree(vol) == before and len(_run_calls(machine)) == 1, "der Austausch begann nie, kein halbes .restore-neu"
    assert machine.containers[NEW_C]["running"] is True and "compose -p deploy start nodvard-deck" in machine.calls
    assert "Starte den nodvard-deck-Dienst wieder" in result.stdout, "die Meldung des EXIT-Traps geht nicht in der Umleitung verloren"


# ---------------------------------------------------------------------------
# Unterbrochener Austausch: Marke im Volume, Wiederaufnahme, backup.sh lehnt ab, Arbeitsordner nie in einer Sicherung
# ---------------------------------------------------------------------------
MARK = ".restore-austausch"


def _old_state_kept(vol: Path) -> dict[str, bytes | None]:
    """Der bisherige Stand nach einem Abbruch beim Beiseiteschieben: was oben noch liegt plus was schon in .restore-alt steckt."""
    kept: dict[str, bytes | None] = {}
    for base in (vol, vol / ".restore-alt"):
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base)
            if not rel.parts[0].startswith(".restore-"):
                kept[str(rel)] = path.read_bytes() if path.is_file() else None
    return dict(sorted(kept.items()))


def _write_raw_archive(path: Path, files: dict[str, bytes]) -> None:
    """tar.gz mit genau diesen Namen (auch ohne `./` davor)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))


def _write_members_archive(path: Path, members: list[tuple[str, str, bytes | str]]) -> None:
    """tar.gz aus `(name, art, inhalt)`; art: `file` (Inhalt = Bytes), `symlink` oder `hardlink` (Inhalt = Ziel)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, kind, content in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(content, bytes)
                info.size = len(content)
                tf.addfile(info, io.BytesIO(content))
            else:
                assert isinstance(content, str)
                info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                info.linkname = content
                tf.addfile(info)


@needs_bash
@needs_gnu_tar
@pytest.mark.parametrize(
    "name",
    ["../boese", "../../ausserhalb/boese", "unterordner/../../../ausserhalb/boese"],
    ids=["ein-Ordner-hoch", "aus-dem-Volume-heraus", "versteckt-im-Pfad"],
)
def test_restore_refuses_an_archive_with_names_that_climb_out_of_the_unpack_folder(machine, backup_env, tmp_path, name):
    # `../boese` waere (vom Entpackordner aus) eine Datei mitten in den noch gueltigen alten Daten, `../../ausserhalb/boese`
    # sogar eine ausserhalb des Volumes. Beides darf weder entstehen noch den alten Stand veraendern.
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_members_archive(archive, [("./lattice.db", "file", b"neu"), (name, "file", b"boese")])
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "FEHLER" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before, "alte Daten vollstaendig und unveraendert, kein .restore-neu zurueckgeblieben"
    assert not (vol / "boese").exists() and not (tmp_path / "ausserhalb").exists(), "nichts entsteht neben oder ausserhalb des Entpackordners"
    assert len(_run_calls(machine)) == 1, "nur das Entpacken, der Austausch begann nie"
    assert machine.containers[NEW_C]["running"] is True and "compose -p deploy start nodvard-deck" in machine.calls


@needs_bash
@needs_gnu_tar
def test_restore_puts_an_absolute_name_inside_the_volume_and_never_at_the_named_place(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    outside = tmp_path / "ausserhalb"
    outside.mkdir()
    target = outside / "boese"
    archive = tmp_path / "neu.tar.gz"
    _write_members_archive(archive, [("./lattice.db", "file", b"neu"), (str(target), "file", b"boese")])
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists() and list(outside.iterdir()) == [], "am genannten Ort entsteht nichts"
    assert (vol / str(target).lstrip("/")).read_bytes() == b"boese", "der fuehrende Schraegstrich wird entfernt, die Datei liegt im Volume"
    assert (vol / "lattice.db").read_bytes() == b"neu"


@needs_bash
@needs_gnu_tar
@pytest.mark.parametrize("goal", ["ausserhalb", "volume"])
def test_restore_refuses_a_name_that_leads_through_a_link_in_the_archive(machine, backup_env, tmp_path, goal):
    # Ein Link im Archiv, dessen Ziel ausserhalb des Entpackordners liegt, und danach eine Datei "durch" den Link: so liesse sich
    # sonst in die gueltigen alten Daten (oder aus dem Volume heraus) schreiben, bevor der Austausch begonnen hat.
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    outside = tmp_path / "ausserhalb"
    outside.mkdir()
    destination = outside if goal == "ausserhalb" else vol
    archive = tmp_path / "neu.tar.gz"
    _write_members_archive(
        archive,
        [("./lattice.db", "file", b"neu"), ("link", "symlink", str(destination)), ("link/boese", "file", b"boese"), ("link/lattice.db", "file", b"ueberschrieben")],
    )
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "FEHLER" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before, "alte Daten unveraendert (auch lattice.db wurde nicht durch den Link ueberschrieben)"
    assert list(outside.iterdir()) == [] and not (vol / "boese").exists()
    assert len(_run_calls(machine)) == 1 and machine.containers[NEW_C]["running"] is True


@needs_bash
@needs_gnu_tar
def test_restore_refuses_a_hard_link_to_a_file_outside_the_unpack_folder(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    outside = tmp_path / "ausserhalb"
    outside.mkdir()
    secret = outside / "geheim"
    secret.write_bytes(b"geheim")
    archive = tmp_path / "neu.tar.gz"
    _write_members_archive(archive, [("./lattice.db", "file", b"neu"), ("kopie", "hardlink", str(secret))])
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "FEHLER" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before and not (vol / "kopie").exists()
    assert secret.read_bytes() == b"geheim" and secret.stat().st_nlink == 1, "die fremde Datei wurde nicht verknuepft"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
@pytest.mark.parametrize("where", ["alt", "neu"])
def test_restore_repeat_after_a_broken_swap_finishes_it_and_keeps_the_old_state_complete_until_then(machine, backup_env, tmp_path, where):
    # Der Austausch bricht mittendrin ab (beim Beiseiteschieben der alten bzw. beim Einsetzen der neuen Daten). Die Marke im
    # Volume haelt das fest; der naechste Lauf macht an der richtigen Stelle weiter, ohne vom alten Stand etwas zu verlieren.
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    first = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SWAP_FAILS=where)
    assert first.returncode != 0 and "mittendrin fehlgeschlagen" in first.stderr and "NICHT gestartet" in first.stderr
    assert ".restore-alt" in first.stderr, "sagt, wo der bisherige Stand liegt"
    assert (vol / MARK).read_text(encoding="utf-8") == f"{where}\n"
    if where == "alt":
        assert _old_state_kept(vol) == _files_only(OLD_DATA), "nichts vom alten Stand fehlt, ein Teil liegt schon in .restore-alt"
    else:
        assert _tree(vol / ".restore-alt") == _files_only(OLD_DATA), "der alte Stand liegt vollstaendig in .restore-alt"
    assert "compose -p deploy start nodvard-deck" not in machine.calls
    log = (tmp_path / "logs" / "restore.log").read_text(encoding="utf-8")
    assert "NICHT gestartet" in log and "wiederholen" in log, "die Meldung steht auch im Protokoll"

    again = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert again.returncode == 0, again.stdout + again.stderr
    assert "frueheres Einspielen" in again.stderr and "Fertig." in again.stdout
    assert _tree(vol) == NEW_DATA, "fertig ausgetauscht, keine Marke, keine Reste"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
@pytest.mark.parametrize("where", ["alt", "neu"])
@pytest.mark.parametrize("second", ["datenstrom-bricht-ab", "strg-c"])
def test_restore_repeat_after_a_broken_swap_never_starts_the_service_even_if_its_unpacking_fails(machine, backup_env, tmp_path, where, second):
    # Frueher wusste der zweite Lauf nichts vom halben Austausch: scheiterte sein Entpacken, meldete er "unveraendert" und
    # startete das Dashboard ohne Datenbank und Schluessel.
    _up(machine)
    vol = _volume(tmp_path)
    new = {"lattice.db": os.urandom(200_000), "jwt_secret.key": b"neuer schluessel"}
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, new)
    assert backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SWAP_FAILS=where).returncode != 0
    fault = {"STUB_RESTORE_FAILS": "1"} if second == "datenstrom-bricht-ab" else {"STUB_INT_ON_UNPACK": "1"}
    again = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), **fault)
    assert again.returncode != 0 and "Fertig." not in again.stdout
    assert "unveraendert" not in again.stderr
    assert "NICHT gestartet" in again.stderr and "wiederholen" in again.stderr and str(archive) in again.stderr
    assert "compose -p deploy start nodvard-deck" not in machine.calls and machine.containers[NEW_C]["running"] is False
    assert (vol / MARK).exists() and not (vol / ".restore-neu").exists()
    if where == "alt":
        assert _old_state_kept(vol) == _files_only(OLD_DATA)
    else:
        assert _tree(vol / ".restore-alt") == _files_only(OLD_DATA)
        assert sorted(p.name for p in vol.iterdir()) == [".restore-alt", MARK], "der halb eingesetzte neue Stand ist weg (Platz)"
    third = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert third.returncode == 0, third.stdout + third.stderr
    assert _tree(vol) == new and machine.containers[NEW_C]["running"] is True


@needs_bash
def test_backup_refuses_after_a_broken_swap_and_neither_stops_nor_starts_the_service(machine, backup_env, tmp_path):
    # Frueher startete backup.sh (etwa aus der root-Crontab) den absichtlich gestoppten Dienst auf dem halben Stand und
    # sicherte diesen als "Fertig".
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    assert backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SWAP_FAILS="1").returncode != 0
    before = len(machine.calls)
    out = tmp_path / "sicherungen"
    result = backup_env("backup.sh", str(out), STUB_VOLUME=str(vol))
    assert result.returncode == 3 and "Fertig" not in result.stdout
    assert "./restore.sh" in result.stderr and "nichts gestartet" in result.stderr, "sagt, was zu tun ist"
    calls = machine.calls[before:]
    assert len(calls) == 2 and calls[0] == "image inspect nodvard-deck:latest" and _is_check_call(calls[1]), calls
    assert machine.containers[NEW_C]["running"] is False
    assert _backups_in(out) == []


@needs_bash
def test_backup_that_cannot_check_the_data_folder_stops_nothing(machine, backup_env, tmp_path):
    _up(machine)
    out = tmp_path / "sicherungen"
    result = backup_env("backup.sh", str(out), STUB_CHECK_FAILS="1")
    assert result.returncode != 0 and "nichts angehalten" in result.stderr
    assert not any(c.startswith(("stop", "compose -p deploy stop", "compose -p deploy start")) for c in machine.calls)
    assert machine.containers[NEW_C]["running"] is True and _backups_in(out) == []


@needs_bash
def test_restore_that_cannot_check_the_data_folder_changes_nothing(machine, backup_env, tmp_path):
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_CHECK_FAILS="1")
    assert result.returncode != 0 and "nichts veraendert" in result.stderr
    assert _tree(vol) == before and not _run_calls(machine)
    assert not any(c.startswith(("stop", "compose -p deploy stop")) for c in machine.calls)
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_backup_leaves_the_work_folders_of_restore_out_of_the_archive(machine, backup_env, tmp_path):
    # Reste eines Einspielens (halb entpackt, ein beiseitegelegter alter Stand) gehoeren nicht in eine Sicherung: frueher
    # liess sich so eine Sicherung nie wieder einspielen.
    _up(machine)
    vol = _volume(tmp_path, {**OLD_DATA, "lattice.db": os.urandom(4096)})
    for name in (".restore-neu/rest", ".restore-alt/rest", ".restore-alt-20260101-000000/rest"):
        (vol / name).parent.mkdir(parents=True, exist_ok=True)
        (vol / name).write_bytes(b"rest")
    out = tmp_path / "sicherungen"
    result = backup_env("backup.sh", str(out), STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    (backup,) = _backups_in(out)
    with tarfile.open(backup, "r:gz") as tf:
        names = tf.getnames()
    assert {"./lattice.db", "./master.key", "./backups/vor-update/kopie.db", "./.versteckt"} <= set(names)
    assert not [n for n in names if ".restore-" in n], names
    # Und die Sicherung laesst sich in ein anderes Volume einspielen.
    target = _volume(tmp_path / "ziel")
    again = backup_env("restore.sh", str(backup), stdin="y\n", STUB_VOLUME=str(target))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _tree(target) == {k: v for k, v in _tree(vol).items() if not k.startswith(".restore-")}


@needs_bash
@pytest.mark.parametrize(
    "leftover",
    ["./.restore-alt/lattice.db", "./.restore-neu/master.key", "./.restore-austausch", ".restore-alt/lattice.db", ".restore-neu/x", "./.restore-alt-20260101-000000/x"],
)
def test_restore_of_a_backup_that_contains_work_folders_of_an_earlier_restore_leaves_them_out(machine, backup_env, tmp_path, leftover):
    # So eine Sicherung (von einer aelteren backup.sh) liess sich nie einspielen: der Austausch scheiterte an jedem Versuch,
    # und jede Wiederholung loeschte vorher den alten Stand. Auch ohne `./` vor den Namen.
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "mit-resten.tar.gz"
    _write_raw_archive(archive, {**{"./" + k: v for k, v in NEW_DATA.items()}, leftover: b"rest"})
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _tree(vol) == NEW_DATA, "nur die Daten, keine Arbeitsordner und keine Marke"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_sets_a_leftover_old_state_aside_instead_of_deleting_it(machine, backup_env, tmp_path):
    # Ein `.restore-alt` ohne Marke (das Loeschen nach einem fertigen Austausch hatte nicht geklappt) wurde frueher blind
    # geloescht. Jetzt wird es mit Zeitstempel beiseitegelegt, und die Meldung sagt, wo es liegt.
    _up(machine)
    _executable(machine.bin / "date", '#!/usr/bin/env bash\necho 20260101-000000\n')
    vol = _volume(tmp_path)
    (vol / ".restore-alt").mkdir()
    (vol / ".restore-alt" / "lattice.db").write_bytes(b"aelterer stand")
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    kept = ".restore-alt-20260101-000000"
    assert (vol / kept / "lattice.db").read_bytes() == b"aelterer stand"
    assert f"/{kept}" in result.stderr, "die Meldung nennt den neuen Ort"
    assert {k: v for k, v in _tree(vol).items() if not k.startswith(kept)} == NEW_DATA


@needs_bash
def test_restore_whose_swap_cannot_even_begin_keeps_the_old_data_and_starts_the_service(machine, backup_env, tmp_path):
    # Der alte Rest laesst sich nicht beiseitelegen (Name schon belegt): dann wurde nichts veraendert, das muss die Meldung
    # sagen, und der Dienst laeuft auf dem alten Stand weiter (ohne den halb entpackten Ordner).
    _up(machine)
    _executable(machine.bin / "date", '#!/usr/bin/env bash\necho 20260101-000000\n')
    vol = _volume(tmp_path)
    for name in (".restore-alt/x", ".restore-alt-20260101-000000/y"):
        (vol / name).parent.mkdir(parents=True, exist_ok=True)
        (vol / name).write_bytes(b"rest")
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "nicht beginnen" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before, "alte Daten und beide Reste unveraendert, kein .restore-neu, keine Marke"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
def test_restore_writes_the_mark_to_disk_before_it_moves_anything(machine, backup_env, tmp_path):
    # Die Marke muss einen Stromausfall ueberstehen: Waere sie danach leer, koennte kein Lauf mehr sicher weitermachen.
    import shlex

    _up(machine)
    trace = tmp_path / "ablauf"
    for tool in ("sync", "mv"):
        _wrap(machine, tool, f'echo "{tool} $*" >> {shlex.quote(str(trace))}')
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    steps = trace.read_text(encoding="utf-8").splitlines()
    syncs = [i for i, s in enumerate(steps) if s == f"sync {MARK}.neu"]
    marks = [i for i, s in enumerate(steps) if s == f"mv -f {MARK}.neu {MARK}"]
    first_old = next(i for i, s in enumerate(steps) if s.startswith("mv -t .restore-alt/ "))
    first_new = next(i for i, s in enumerate(steps) if s.startswith("mv -t . "))
    assert len(syncs) == len(marks) == 2, steps
    assert syncs[0] < marks[0] < first_old < syncs[1] < marks[1] < first_new, steps


@needs_bash
def test_restore_writes_the_new_data_to_disk_before_it_deletes_the_mark_and_the_old_state(machine, backup_env, tmp_path):
    # Die Marke und der alte Stand sind das Einzige, was nach einem Stromausfall noch weiterhilft. Werden sie geloescht, bevor
    # die eingesetzten neuen Daten auf dem Datentraeger sind, kann danach eine leere Datenbank ohne Rueckweg uebrig bleiben.
    import shlex

    _up(machine)
    trace = tmp_path / "ablauf"
    for tool in ("sync", "mv", "rmdir", "rm"):
        _wrap(machine, tool, f'echo "{tool} $*" >> {shlex.quote(str(trace))}')
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 0, result.stdout + result.stderr
    steps = [s.strip() for s in trace.read_text(encoding="utf-8").splitlines()]
    last_move = max(i for i, s in enumerate(steps) if s.startswith("mv -t . "))
    drop_mark = steps.index(f"rm -f {MARK}")
    drop_old = steps.index("rm -rf .restore-alt")
    full_syncs = [i for i, s in enumerate(steps) if s == "sync"]
    assert any(last_move < i < drop_mark for i in full_syncs), steps
    assert drop_mark < drop_old, steps


@needs_bash
def test_restore_whose_mark_cannot_be_written_changes_nothing_and_starts_the_service(machine, backup_env, tmp_path):
    # Ohne Marke darf der Austausch nicht beginnen: Ein Abbruch danach waere beim naechsten Lauf nicht mehr zu erkennen.
    _up(machine)
    _wrap(machine, "mv", f'if [ "$*" = "-f {MARK}.neu {MARK}" ]; then echo "mv: No space left on device" >&2; exit 1; fi')
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    result = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode != 0 and "nicht beginnen" in result.stderr and "unveraendert" in result.stderr
    assert _tree(vol) == before, "alte Daten unveraendert, kein .restore-neu, keine Marke"
    assert machine.containers[NEW_C]["running"] is True


def _break_the_swap(machine: Machine, backup_env, tmp_path: Path, where: str = "alt") -> Path:
    """Ein Einspielen, das mitten im Austausch stehen bleibt: Marke da, Dienst gestoppt. Gibt den Volume-Ordner zurueck."""
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    assert backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol), STUB_SWAP_FAILS=where).returncode == 1
    assert (vol / MARK).exists() and machine.containers[NEW_C]["running"] is False
    return vol


def _spoil_the_mark(vol: Path, kind: str) -> None:
    mark = vol / MARK
    mark.unlink()
    if kind == "ordner":  # nicht lesbar als Datei
        mark.mkdir()
    else:
        mark.write_text(kind, encoding="utf-8")


BROKEN_MARKS = ["", "kaputt\n", "ordner"]


@needs_bash
@pytest.mark.parametrize("content", BROKEN_MARKS)
def test_restore_with_a_damaged_mark_moves_nothing_keeps_the_service_stopped_and_names_the_way_by_hand(machine, backup_env, tmp_path, content):
    # Ist die Marke leer oder unlesbar, weiss niemand, wo der Austausch stand: nichts verschieben, nichts loeschen, nichts starten.
    # Die Meldung darf nicht "mit derselben Datei wiederholen" raten (das hilft nie), sondern nennt den Weg von Hand.
    vol = _break_the_swap(machine, backup_env, tmp_path)
    _spoil_the_mark(vol, content)
    archive = tmp_path / "neu.tar.gz"
    before, calls_before = _tree(vol), len(machine.calls)
    again = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
    assert again.returncode == 5 and "Fertig." not in again.stdout and "unveraendert" not in again.stderr
    for part in (".restore-alt", ".restore-austausch", "docker compose -p deploy run --rm --no-deps --entrypoint rm nodvard-deck -f /app/data/.restore-austausch"):
        assert part in again.stderr, (part, again.stderr)
    assert "wiederholen" not in again.stderr, "kein Rat zum Wiederholen, der hier nie hilft"
    assert _tree(vol) == before and _old_state_kept(vol) == _files_only(OLD_DATA), "nichts verschoben, nichts geloescht"
    new_calls = machine.calls[calls_before:]
    assert len(new_calls) == 1 and _is_check_call(new_calls[0]), "nur die Pruefung, nichts angehalten oder gestartet"
    assert machine.containers[NEW_C]["running"] is False
    assert "FEHLER" in (tmp_path / "logs" / "restore.log").read_text(encoding="utf-8")


@needs_bash
@pytest.mark.parametrize("content", BROKEN_MARKS)
def test_backup_with_a_damaged_mark_refuses_with_its_own_code_and_changes_nothing(machine, backup_env, tmp_path, content):
    vol = _break_the_swap(machine, backup_env, tmp_path)
    _spoil_the_mark(vol, content)
    before, calls_before = _tree(vol), len(machine.calls)
    out = tmp_path / "sicherungen"
    result = backup_env("backup.sh", str(out), STUB_VOLUME=str(vol))
    assert result.returncode == 5 and "Fertig" not in result.stdout
    assert ".restore-alt" in result.stderr and "-f /app/data/.restore-austausch" in result.stderr and "nichts automatisch geloescht" in result.stderr
    assert _tree(vol) == before and _backups_in(out) == []
    new_calls = machine.calls[calls_before:]
    assert [c for c in new_calls if not c.startswith("image inspect")] == [c for c in new_calls if _is_check_call(c)] and len(new_calls) == 2
    assert machine.containers[NEW_C]["running"] is False


@needs_bash
@pytest.mark.parametrize("where", ["alt", "neu"])
@pytest.mark.parametrize("answer", ["y\n", "n\n"])
def test_restore_ndbak_refuses_while_a_swap_is_unfinished_and_never_restarts_the_service(machine, backup_env, tmp_path, where, answer):
    # Frueher merkte `restore.sh x.ndbak` trotzdem vor und rief `docker compose restart` auf: das startete den gestoppten Dienst
    # auf halbem Stand.
    vol = _break_the_swap(machine, backup_env, tmp_path, where)
    before, calls_before = _tree(vol), len(machine.calls)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    folder = tmp_path / "ndbak"
    folder.mkdir()
    source = folder / "nodvard-deck-sicherung-20261001-023000.ndbak"
    source.write_bytes(os.urandom(512))
    result = backup_env("restore.sh", str(source), stdin=answer, STUB_VOLUME=str(vol), TMPDIR=str(scratch))
    assert result.returncode == 3, result.stdout + result.stderr
    assert "./restore.sh" in result.stderr and "tar.gz" in result.stderr and "nichts vorgemerkt" in result.stderr, "nennt den Weg"
    new_calls = machine.calls[calls_before:]
    assert len(new_calls) == 1 and _is_check_call(new_calls[0]), new_calls
    assert not Path(str(machine.state_file) + ".ndbak").exists(), "nichts vorgemerkt"
    assert machine.containers[NEW_C]["running"] is False and _tree(vol) == before
    assert list(scratch.iterdir()) == [] and "Vormerken" not in result.stdout and "Verschluesselte Sicherung" not in result.stdout


@needs_bash
@pytest.mark.parametrize("content", BROKEN_MARKS)
def test_restore_ndbak_with_a_damaged_mark_refuses_with_its_own_code(machine, backup_env, tmp_path, content):
    vol = _break_the_swap(machine, backup_env, tmp_path)
    _spoil_the_mark(vol, content)
    calls_before = len(machine.calls)
    source = tmp_path / "sicherung.ndbak"
    source.write_bytes(os.urandom(512))
    result = backup_env("restore.sh", str(source), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 5 and ".restore-alt" in result.stderr
    assert len(machine.calls) == calls_before + 1 and not Path(str(machine.state_file) + ".ndbak").exists()
    assert machine.containers[NEW_C]["running"] is False


@needs_bash
def test_restore_ndbak_that_cannot_check_the_data_folder_does_not_queue_anything(machine, backup_env, tmp_path):
    _up(machine)
    source = tmp_path / "sicherung.ndbak"
    source.write_bytes(os.urandom(512))
    result = backup_env("restore.sh", str(source), stdin="y\n", STUB_CHECK_FAILS="1")
    assert result.returncode == 1 and "nichts vorgemerkt" in result.stderr
    assert len(machine.calls) == 1 and not Path(str(machine.state_file) + ".ndbak").exists()


@needs_bash
@pytest.mark.skipif(shutil.which("setsid") is None, reason="braucht setsid (util-linux) fuer ein echtes Terminal")
def test_restore_finishes_and_starts_the_service_when_the_ssh_terminal_goes_away_while_unpacking(machine, tmp_path):
    # Wie unter sshd: restore.sh laeuft an einem echten Terminal (Sitzungsfuehrer mit Steuerterminal). Mitten im Entpacken
    # reisst die Verbindung ab, das Terminal ist weg: jedes Schreiben darauf scheitert danach. Frueher beendete sich das
    # Skript am naechsten `echo`, der Austausch lief nie, und der Dienst blieb ohne Meldung gestoppt.
    import select

    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    gate = tmp_path / "tor"
    master, slave = os.openpty()
    proc = subprocess.Popen(
        ["setsid", "-c", "-w", "bash", str(_script_copy(tmp_path, "restore.sh")), str(archive)],
        stdin=slave, stdout=slave, stderr=slave, env=machine.env(STUB_VOLUME=str(vol), STUB_UNPACK_GATE=str(gate)),
    )
    os.close(slave)
    try:
        os.write(master, b"y\n")
        deadline = time.time() + 30
        while not Path(f"{gate}.started").exists():
            assert time.time() < deadline and proc.poll() is None, "das Entpacken hat nicht begonnen"
            if select.select([master], [], [], 0.1)[0]:
                try:
                    os.read(master, 4096)
                except OSError:
                    break
    finally:
        os.close(master)  # aufgelegt: wie sshd beim Verbindungsabbruch
    Path(f"{gate}.go").touch()
    rc = proc.wait(timeout=60)
    log_file = tmp_path / "logs" / "restore.log"
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert (rc, len(_run_calls(machine))) == (0, 2), (machine.calls, log)
    assert _tree(vol) == NEW_DATA
    assert machine.containers[NEW_C]["running"] is True
    assert "Tausche die Daten aus" in log and "Fertig." in log, "wie es ausging, steht im Protokoll"


def test_ci_runs_the_root_tests_of_the_deploy_scripts_as_root():
    # Der Runner ist nicht root: ohne einen eigenen Lauf mit sudo werden die Tests mit echten Besitzern und Rechten dort nur
    # uebersprungen. `-m needs_root` waehlt nur etwas aus, wenn es den Marker wirklich gibt.
    import re

    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = [s for s in re.findall(r"^\s*- run: (sudo .*)$", text, re.MULTILINE) if "backend/tests/test_deploy_switch.py" in s]
    assert len(steps) == 1, steps
    step = steps[0]
    assert re.match(r'sudo -E env "PATH=\$PATH" ', step) and "python -m pytest" in step and re.search(r"-m needs_root\b", step)
    assert "-p no:cacheprovider" in step and "PYTHONDONTWRITEBYTECODE=1" in step, "nichts root-Eigenes im Checkout"
    marked = [
        name for name, func in globals().items()
        if name.startswith("test_") and any(m.name == "needs_root" for m in getattr(func, "pytestmark", []))
    ]
    assert len(marked) >= 3, marked
    assert "needs_root:" in (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"), "Marker registriert"


@needs_bash
def test_restore_names_a_file_the_caller_cannot_read(machine, backup_env, tmp_path):
    _up(machine)
    missing = backup_env("restore.sh", str(tmp_path / "gibt-es-nicht.tar.gz"), stdin="y\n")
    assert missing.returncode != 0 and "Nicht gefunden" in missing.stderr
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        archive = tmp_path / "gesperrt.tar.gz"
        _write_archive(archive)
        archive.chmod(0)
        result = backup_env("restore.sh", str(archive), stdin="y\n")
        assert result.returncode != 0 and "Nicht lesbar" in result.stderr and "sudo" in result.stderr
        assert machine.calls == []


# ---------------------------------------------------------------------------
# backup.sh / restore.sh: eine gemeinsame Sperre gegen gleichzeitige Laeufe
# ---------------------------------------------------------------------------
needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="braucht flock (util-linux)")
BUSY = "Es laeuft schon eine Sicherung oder ein Einspielen (siehe restore.log). Bitte warten, bis es fertig ist."


def _lock_path(tmp_path: Path) -> Path:
    return tmp_path / "skript-kopie" / ".backup-restore.lock"


class _HeldLock:
    """Ein anderer Prozess haelt die Sperre (echtes `flock` mit `sleep`), bis der Block endet."""

    def __init__(self, tmp_path: Path) -> None:
        self.path = _lock_path(tmp_path)

    def __enter__(self) -> Path:
        self.path.parent.mkdir(exist_ok=True)
        self.path.touch()
        # `exec sleep`: derselbe Prozess haelt die Sperre (ein `flock <datei> sleep` ginge ueber einen Kindprozess, der beim
        # Beenden ueberlebt).
        self.proc = subprocess.Popen(["bash", "-c", 'exec 9< "$1" && flock 9 && exec sleep 120', "halter", str(self.path)])
        deadline = time.time() + 20
        while subprocess.run(["flock", "-n", str(self.path), "true"], check=False).returncode == 0:
            assert time.time() < deadline and self.proc.poll() is None, "die Sperre wurde nicht gehalten"
            time.sleep(0.05)
        return self.path

    def __exit__(self, *exc) -> None:
        self.proc.terminate()
        self.proc.wait()


def _ndbak_file(tmp_path: Path) -> Path:
    source = tmp_path / "sicherung.ndbak"
    source.write_bytes(os.urandom(512))
    return source


@needs_bash
@needs_flock
@pytest.mark.parametrize("kind", ["tar.gz", "ndbak"])
def test_restore_is_refused_while_another_run_holds_the_lock(machine, backup_env, tmp_path, kind):
    _up(machine)
    vol = _volume(tmp_path)
    before = _tree(vol)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    source = archive if kind == "tar.gz" else _ndbak_file(tmp_path)
    with _HeldLock(tmp_path):
        result = backup_env("restore.sh", str(source), stdin="y\n", STUB_VOLUME=str(vol))
    assert result.returncode == 4, result.stdout + result.stderr
    assert BUSY in result.stderr and "nichts veraendert" in result.stderr
    assert machine.calls == [], "kein einziger Docker-Aufruf"
    assert _tree(vol) == before and machine.containers[NEW_C]["running"] is True
    if kind == "tar.gz":
        assert BUSY in (tmp_path / "logs" / "restore.log").read_text(encoding="utf-8"), "steht auch im Protokoll"


@needs_bash
@needs_flock
def test_backup_is_refused_while_another_run_holds_the_lock(machine, backup_env, tmp_path):
    _up(machine)
    out = tmp_path / "sicherungen"
    with _HeldLock(tmp_path):
        result = backup_env("backup.sh", str(out), STUB_VOLUME=str(_volume(tmp_path)))
    assert result.returncode == 4 and BUSY in result.stderr, result.stdout + result.stderr
    assert machine.calls == [] and not out.exists(), "nichts angehalten, nichts angelegt"
    assert machine.containers[NEW_C]["running"] is True


@needs_bash
@needs_flock
def test_a_second_run_is_refused_while_a_restore_really_runs_even_after_hangup_and_works_again_afterwards(machine, backup_env, tmp_path):
    # Der echte Fall: Die SSH-Verbindung reisst ab, Lauf 1 laeuft (HUP wird ignoriert) im Hintergrund weiter, und jemand startet
    # restore.sh noch einmal; oder eine Cron-Sicherung kommt dazwischen.
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    gate = tmp_path / "tor"
    first = subprocess.Popen(
        ["bash", str(_script_copy(tmp_path, "restore.sh")), str(archive)], env=machine.env(STUB_VOLUME=str(vol), STUB_UNPACK_GATE=str(gate)),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        first.stdin.write("y\n")
        first.stdin.flush()
        deadline = time.time() + 30
        while not Path(f"{gate}.started").exists():
            assert time.time() < deadline and first.poll() is None, "das Entpacken hat nicht begonnen"
            time.sleep(0.05)
        first.send_signal(signal.SIGHUP)
        time.sleep(0.2)
        calls_before = len(machine.calls)
        out = tmp_path / "sicherungen"
        backup = backup_env("backup.sh", str(out), STUB_VOLUME=str(vol))
        again = backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol))
        assert (backup.returncode, again.returncode) == (4, 4), (backup.stderr, again.stderr)
        assert BUSY in backup.stderr and BUSY in again.stderr
        assert len(machine.calls) == calls_before and not out.exists(), "die abgewiesenen Laeufe haben nichts angefasst"
    finally:
        Path(f"{gate}.go").touch()
        stdout, stderr = first.communicate(timeout=60)
    assert first.returncode == 0, stdout + stderr
    assert _tree(vol) == NEW_DATA and machine.containers[NEW_C]["running"] is True
    # Lauf 1 ist zu Ende: jetzt geht ein neuer Lauf, auch ein anderes Skript.
    assert backup_env("backup.sh", str(out)).returncode == 0 and len(_backups_in(out)) == 1
    assert backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol)).returncode == 0


@needs_bash
@needs_flock
def test_the_lock_is_released_when_a_run_fails(machine, backup_env, tmp_path):
    _up(machine)
    archive = tmp_path / "kaputt.tar.gz"
    archive.write_bytes(b"kein gzip")
    assert backup_env("restore.sh", str(archive), stdin="y\n").returncode == 1
    assert backup_env("backup.sh", str(tmp_path / "ziel"), STUB_TAR_EMPTY="1").returncode == 1
    out = tmp_path / "ziel2"
    assert backup_env("backup.sh", str(out)).returncode == 0 and len(_backups_in(out)) == 1


@needs_bash
@needs_flock
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "restore.ndbak"])
@pytest.mark.parametrize("planted", ["link-ins-leere", "pipe"])
def test_a_link_or_pipe_in_place_of_the_lock_file_is_never_followed(machine, backup_env, tmp_path, script, planted):
    # Wer im deploy-Ordner schreiben darf, legte vorab einen Link unter dem Namen der Sperrdatei ab: als root legte `: >>` die
    # Datei am Ziel an und setzte sie auf 0644 (etwa /etc/nologin), eine Pipe liess das Skript (z. B. aus Cron) ewig warten.
    _up(machine)
    lock = _lock_path(tmp_path)
    lock.parent.mkdir(exist_ok=True)
    target = tmp_path / "fremd" / "nologin"
    target.parent.mkdir()
    if planted == "pipe":
        os.mkfifo(target)
    lock.symlink_to(target)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    if script == "backup.sh":
        result = backup_env("backup.sh", str(tmp_path / "sicherungen"))
    else:
        source = archive if script == "restore.sh" else _ndbak_file(tmp_path)
        result = backup_env("restore.sh", str(source), stdin="y\n")
    assert result.returncode == 1 and "keine gewoehnliche Datei" in result.stderr and "rm " in result.stderr, result.stderr
    assert machine.calls == [], "nichts angehalten, nichts gestartet"
    assert lock.is_symlink() and (target.is_fifo() if planted == "pipe" else not target.exists()), "dem Link nie gefolgt"


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "restore.ndbak"])
def test_without_flock_the_scripts_warn_and_carry_on_without_a_lock(machine, backup_env, tmp_path, script):
    # PATH ohne `flock` (aber mit allem anderen, was die Skripte brauchen).
    _up(machine)
    tools = tmp_path / "ohne-flock"
    tools.mkdir()
    for name in ("bash", "sh", "dirname", "mktemp", "tar", "date", "id", "chmod", "chown", "rm", "cat", "mv", "ls", "stat", "wc", "env",
                 "sync", "find", "mkdir", "gzip", "cp", "basename", "head", "tr", "sleep", "rmdir", "touch", "python3", "setfacl"):
        real = shutil.which(name)
        if real:
            (tools / name).symlink_to(real)
    assert not (tools / "flock").exists()
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive)
    args = {"backup.sh": (str(tmp_path / "ziel"),), "restore.sh": (str(archive),), "restore.ndbak": (str(_ndbak_file(tmp_path)),)}[script]
    env_path = f"{machine.bin}{os.pathsep}{tools}"
    result = backup_env(script.replace(".ndbak", ".sh"), *args, stdin="n\n" if script == "restore.ndbak" else "y\n", PATH=env_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "flock" in result.stderr and "ohne Sperre" in result.stderr
    assert not _lock_path(tmp_path).exists()


@needs_bash
@needs_flock
def test_the_lock_never_reaches_docker(machine, backup_env, tmp_path):
    # Ein haengender Hilfsprozess (etwa ein Docker-Aufruf, dessen Skript abgebrochen wurde) soll die Sperre nicht ueber das Ende des
    # Skripts hinaus halten: Deskriptor 9 bleibt allen Docker-Aufrufen verschlossen, in allen Zweigen.
    _up(machine)
    vol = _volume(tmp_path)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive, NEW_DATA)
    assert backup_env("backup.sh", str(tmp_path / "ziel")).returncode == 0
    assert backup_env("restore.sh", str(archive), stdin="y\n", STUB_VOLUME=str(vol)).returncode == 0
    assert backup_env("restore.sh", str(_ndbak_file(tmp_path)), stdin="y\n").returncode == 0
    assert Path(str(machine.state_file) + ".ndbak").exists() and "compose -p deploy restart nodvard-deck" in machine.calls, "auch der Neustart"
    assert not Path(str(machine.state_file) + ".fd9").exists(), Path(str(machine.state_file) + ".fd9").read_text(encoding="utf-8")


@needs_bash
@needs_flock
def test_the_lock_is_held_for_the_whole_run(machine, backup_env, tmp_path):
    # Gegenprobe zum Test davor: Waehrend des Laufs ist die Sperre fuer jeden anderen belegt (ein Hilfsprogramm des Skripts
    # versucht es selbst).
    _up(machine)
    out = tmp_path / "ziel"
    holder = tmp_path / "haelt-fest"
    _wrap(machine, "date", f'flock -n -E 200 "{_lock_path(tmp_path)}" true; echo $? >> "{holder}"')
    assert backup_env("backup.sh", str(out)).returncode == 0
    assert set(holder.read_text(encoding="utf-8").split()) == {"200"}, "waehrend des Laufs war die Sperre belegt"


@needs_bash
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_sudo_hands_the_lock_file_and_the_log_to_the_sudo_user(machine, backup_env, tmp_path, script):
    # Als root (per sudo) angelegt, gehoerten Sperrdatei und Protokoll root: spaetere Laeufe ohne sudo schrieben still kein Protokoll.
    _up(machine)
    _fake_uid(machine)
    _wrap(machine, "chown", 'echo "$*" >> "$STUB_STATE.chown"')
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive)
    args = (str(tmp_path / "ziel"),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n", STUB_ID_U="0", SUDO_UID="12345", SUDO_GID="23456")
    assert result.returncode == 0, result.stdout + result.stderr
    chowns = Path(str(machine.state_file) + ".chown").read_text(encoding="utf-8").splitlines()
    assert f"-h 12345:23456 {_lock_path(tmp_path)}" in chowns, chowns
    if script == "restore.sh":
        assert f"-h 12345:23456 {tmp_path / 'logs' / 'restore.log'}" in chowns, chowns
    # Ohne sudo (SUDO_UID fehlt) wird an den beiden Dateien nichts umgehaengt.
    chown_log = Path(str(machine.state_file) + ".chown")
    chown_log.unlink()
    plain = backup_env(script, *args, stdin="y\n", STUB_ID_U="0")
    assert plain.returncode == 0
    left = chown_log.read_text(encoding="utf-8").splitlines() if chown_log.exists() else []
    assert not [c for c in left if str(_lock_path(tmp_path)) in c or "restore.log" in c], left


@needs_bash
@needs_root
@pytest.mark.parametrize("script", ["backup.sh", "restore.sh"])
def test_started_with_sudo_the_lock_file_and_the_log_belong_to_the_sudo_user(machine, backup_env, tmp_path, script):
    _up(machine)
    archive = tmp_path / "neu.tar.gz"
    _write_archive(archive)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "restore.log").write_text("alt\n", encoding="utf-8")  # von einem fruehren Lauf als root
    args = (str(tmp_path / "ziel"),) if script == "backup.sh" else (str(archive),)
    result = backup_env(script, *args, stdin="y\n", SUDO_UID="12345", SUDO_GID="23456")
    assert result.returncode == 0, result.stdout + result.stderr
    lock = _lock_path(tmp_path)
    assert (lock.stat().st_uid, lock.stat().st_gid) == (12345, 23456) and stat.S_IMODE(lock.stat().st_mode) == 0o644
    if script == "restore.sh":
        log = logs / "restore.log"
        assert (log.stat().st_uid, log.stat().st_gid) == (12345, 23456) and log.read_text(encoding="utf-8").startswith("alt\n")


@needs_bash
@needs_root
@needs_flock
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="braucht setpriv (util-linux), um als gewoehnlicher Benutzer zu laufen")
def test_a_user_can_run_while_the_lock_file_belongs_to_root_and_is_still_kept_out_by_a_held_lock(machine, backup_env, tmp_path):
    # Die Sperrdatei gehoert root (angelegt von einem Lauf als root, z. B. aus der root-Crontab): ein Lauf als gewoehnlicher
    # Benutzer oeffnet sie nur lesend und kann trotzdem sperren. Die Skripte laufen aus einer Kopie im Temp-Ordner.
    import shlex

    _up(machine)
    uid = 65534
    # Der Benutzer muss durch die Temp-Ordner kommen und die Attrappen-Dateien schreiben duerfen.
    folder = tmp_path
    while folder != folder.parent:
        if not folder.stat().st_mode & 0o001:
            folder.chmod(folder.stat().st_mode | 0o001)
        folder = folder.parent
    tmp_path.chmod(0o777)
    machine.state_file.chmod(0o666)
    for stub in machine.bin.iterdir():
        stub.chmod(0o755)
    script = _script_copy(tmp_path, "backup.sh")
    script.parent.chmod(0o755)
    lock = _lock_path(tmp_path)
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o644)
    os.chown(lock, 0, 0)
    out = tmp_path / "ziel"
    out.mkdir()
    out.chmod(0o777)

    def as_user() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "bash", str(script), str(out)],
            env=machine.env(), capture_output=True, text=True, timeout=60, check=False,
        )

    with _HeldLock(tmp_path):
        busy = as_user()
    assert busy.returncode == 4 and BUSY in busy.stderr, (busy.stdout, busy.stderr)
    assert machine.calls == []
    free = as_user()
    assert free.returncode == 0, shlex.join(free.args) + free.stdout + free.stderr
    assert "WARNUNG" not in free.stderr and (lock.stat().st_uid, stat.S_IMODE(lock.stat().st_mode)) == (0, 0o644)
    (archive,) = _backups_in(out)
    assert archive.stat().st_uid == uid


# ---------------------------------------------------------------------------
# restore.sh mit *.ndbak: der Container braucht einen Pfad -> Kopie mit passenden Rechten, danach weg
# ---------------------------------------------------------------------------
def _ndbak_run(machine: Machine, backup_env, tmp_path: Path, **kwargs):
    """restore.sh mit einer .ndbak in einem Ordner, den der Container nicht betreten darf. Gibt (Ergebnis, Quelle, Scratch-Ordner) zurueck."""
    folder = tmp_path / "nur-fuer-root"
    folder.mkdir()
    folder.chmod(0o700)
    source = folder / "nodvard-deck-sicherung-20261001-023000.ndbak"
    source.write_bytes(os.urandom(2048))
    source.chmod(0o600)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    result = backup_env("restore.sh", str(source), stdin="n\n", TMPDIR=str(scratch), STUB_ROOT_OWNED_DIR=str(folder), **kwargs)
    return result, source, scratch


def _ndbak_info(machine: Machine, source: Path, scratch: Path) -> dict:
    """Was der Container unter /backup gesehen hat; prueft nebenbei, was fuer jeden Zweig gilt."""
    import hashlib

    (info,) = json.loads(Path(str(machine.state_file) + ".ndbak").read_text(encoding="utf-8"))
    assert info["dst"] == "/backup" and info["arg"] == f"/backup/{source.name}"
    assert Path(info["src"]).parent == scratch and info["src"] != str(source.parent), "nicht der Originalordner, sondern eine Kopie"
    assert info["files"][source.name]["sha"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert list(scratch.iterdir()) == [], "die Kopie ist wieder geloescht"
    assert source.exists(), "das Original bleibt"
    assert not any(c.startswith(("stop", "compose -p deploy stop")) for c in machine.calls), "die .ndbak-Vormerkung stoppt nichts"
    return info


def _record_setfacl(machine: Machine, rc: int = 0) -> Path:
    _executable(machine.bin / "setfacl", f'#!/usr/bin/env bash\necho "$*" >> "$STUB_STATE.setfacl"\nexit {rc}\n')
    return Path(str(machine.state_file) + ".setfacl")


@needs_bash
@needs_root
def test_restore_ndbak_as_root_gives_the_copy_to_1000_and_keeps_the_folder_root_owned(machine, backup_env, tmp_path):
    # Der Ordner gehoert nie 1000 (sonst koennte 1000 zwischen dem Kopieren und dem Setzen der Rechte die Datei gegen
    # einen Link tauschen, den root dann umhaengt): die Datei gehoert 1000, der Ordner bleibt root und wird erst danach geoeffnet.
    _up(machine)
    result, source, scratch = _ndbak_run(machine, backup_env, tmp_path, from_copy=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Vorgemerkt" in result.stdout
    info = _ndbak_info(machine, source, scratch)
    assert (info["dir_uid"], info["dir_mode"]) == (0, 0o711)
    assert (info["files"][source.name]["uid"], info["files"][source.name]["mode"]) == (1000, 0o600)


@needs_bash
def test_restore_ndbak_as_user_1000_keeps_everything_private_and_needs_no_acl(machine, backup_env, tmp_path):
    _up(machine)
    _fake_uid(machine)
    setfacl_log = _record_setfacl(machine)
    result, source, scratch = _ndbak_run(machine, backup_env, tmp_path, STUB_ID_U="1000")
    assert result.returncode == 0, result.stdout + result.stderr
    info = _ndbak_info(machine, source, scratch)
    assert (info["dir_mode"], info["files"][source.name]["mode"]) == (0o700, 0o600)
    assert not setfacl_log.exists()


@needs_bash
def test_restore_ndbak_as_another_user_opens_only_that_folder_and_file_for_1000(machine, backup_env, tmp_path):
    # Nicht root, nicht 1000: kein 0644/0711 mehr (die Kopie waere fuer alle lokalen Benutzer lesbar), sondern ein ACL-Eintrag
    # nur fuer 1000 auf genau diesen Ordner und diese Datei.
    _up(machine)
    _fake_uid(machine)
    setfacl_log = _record_setfacl(machine)
    result, source, scratch = _ndbak_run(machine, backup_env, tmp_path, STUB_ID_U="1001")
    assert result.returncode == 0, result.stdout + result.stderr
    info = _ndbak_info(machine, source, scratch)
    assert (info["dir_mode"], info["files"][source.name]["mode"]) == (0o700, 0o600), "fuer andere bleibt alles zu"
    assert setfacl_log.read_text(encoding="utf-8").splitlines() == [
        f"-m u:1000:x {info['src']}",
        f"-m u:1000:r {info['src']}/{source.name}",
    ]


@needs_bash
def test_restore_ndbak_as_another_user_without_working_acl_asks_for_sudo_and_leaves_no_copy(machine, backup_env, tmp_path):
    _up(machine)
    _fake_uid(machine)
    setfacl_log = _record_setfacl(machine, rc=1)
    result, _source, scratch = _ndbak_run(machine, backup_env, tmp_path, STUB_ID_U="1001")
    assert result.returncode != 0 and "FEHLER" in result.stderr and "sudo" in result.stderr and "setfacl" in result.stderr
    assert not _run_calls(machine), "der Container wurde gar nicht erst gestartet"
    assert list(scratch.iterdir()) == [], "auch die abgebrochene Kopie ist weg"
    assert len(setfacl_log.read_text(encoding="utf-8").splitlines()) == 1, "nach dem ersten Fehler kein zweiter Versuch"


@needs_bash
def test_restore_ndbak_removes_the_copy_also_when_the_check_fails(machine, backup_env, tmp_path):
    _up(machine)
    _fake_uid(machine)
    source = tmp_path / "falsches-passwort.ndbak"
    source.write_bytes(b"verschluesselt")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    result = backup_env("restore.sh", str(source), stdin="n\n", TMPDIR=str(scratch), STUB_NDBAK_FAILS="1", STUB_ID_U="1000")
    assert result.returncode != 0
    assert list(scratch.iterdir()) == []


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
