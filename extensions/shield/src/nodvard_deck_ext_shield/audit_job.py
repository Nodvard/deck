"""Lynis-Härtungs-Audit entkoppelt vom SSH-Kanal: reine Befehle und Auswertung, Ausführen und Speichern in `defender.py`."""

from __future__ import annotations

import re
import shlex

from . import antivirus as av
from . import detached as dt

AUDIT_DIR = "/var/lib/nodvard-shield-audit"
AUDITS = dt.JobKind(prefix="audit", base_dir=AUDIT_DIR, unit_prefix="nodvard-shield-audit-", label="Nodvard Shield Audit")
REPORT_FILE = "/var/log/lynis-report.dat"
# Lynis legt als root seine PID-Datei unter /var/run an (auf neueren Systemen ein Verweis auf /run).
LYNIS_PID_FILES = ("/run/lynis.pid", "/var/run/lynis.pid")
# Obergrenze eines Laufs: Lynis braucht auf einem Docker-Host mit vielen Benutzern gut eine Stunde, ausgelastet länger.
LIMIT_S = 3 * 60 * 60
KILL_AFTER_S = 60  # Sekunden zwischen TERM und KILL bei der Obergrenze
NICE = 19
# Vom Protokoll eines fertigen Laufs reicht das Ende, der Bericht steht ganz hinten.
TAIL_BYTES = 256 * 1024

_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
_LYNIS_RC_RE = re.compile(r"^@@lynis-rc=(-?\d+)$")

BUSY_MESSAGE = (
    "Auf dem Server läuft schon ein Lynis-Audit (von Hand gestartet oder noch von früher). "
    "Es wurde kein zweites gestartet – versuch es später noch einmal."
)
NOT_INSTALLED_MESSAGE = "Lynis ist auf diesem Server nicht installiert."
INCOMPLETE_MESSAGE = "Der Lynis-Bericht ist unvollständig – Lynis wurde vorzeitig beendet. Starte das Audit neu."
LOST_MESSAGE = "Das Audit wurde auf dem Server abgebrochen (zum Beispiel durch einen Neustart des Servers). Starte es neu."
NOT_STARTED_MESSAGE = "Das Audit wurde auf dem Server nicht gefunden – es ist wohl nicht gestartet."
UNREADABLE_MESSAGE = "Das Audit hat keine lesbare Antwort geliefert. Starte es neu."
KILLED_MESSAGE = (
    "Lynis wurde auf dem Server beendet, bevor es fertig war (Rückgabecode 137). Meist war der Arbeitsspeicher knapp, "
    "oder jemand hat den Prozess beendet. Starte das Audit neu, wenn der Server weniger zu tun hat."
)


def too_long_message(limit_s: int = LIMIT_S) -> str:
    return f"Das Audit hat länger als {av.duration_text(limit_s)} gedauert und wurde auf dem Server beendet."


def stuck_message(limit_s: int = LIMIT_S) -> str:
    return (f"Das Audit hat länger als {av.duration_text(limit_s)} gedauert. Lynis ließ sich auf dem Server nicht "
            "beenden – sieh dort nach (Prozess „lynis“).")


def unreachable_at_limit_message(reason: str, limit_s: int = LIMIT_S) -> str:
    return (f"Das Audit hat länger als {av.duration_text(limit_s)} gedauert. Der Server war nicht erreichbar, um Lynis "
            f"dort zu beenden ({reason}). Sieh dort nach, ob Lynis noch läuft.")


def stale_message(rc: int | None) -> str:
    code = f" (Rückgabecode {rc})" if rc is not None else ""
    return (f"Lynis hat keinen neuen Bericht geschrieben{code}. Ein älterer Bericht zählt nicht als Ergebnis. "
            "Sieh auf dem Server in /var/log/lynis.log nach.")


def _checked_path(path: str) -> str:
    if not isinstance(path, str) or not _PATH_RE.match(path) or "/../" in f"{path}/":
        raise ValueError("Ungültiger Pfad.")
    return shlex.quote(path)


def lynis_command(
    run_id: str, *, base_dir: str = AUDIT_DIR, limit_s: int = LIMIT_S, report: str = REPORT_FILE,
    pid_files: tuple[str, ...] = LYNIS_PID_FILES,
) -> str:
    """Shell-Befehl für den entkoppelten Lauf (root, Ausgabe in `<lauf>.log`); `parse_result` liest seine Marker.

    Marker: `@@nolynis`, `@@busy`, `@@timeout`, `@@killed` oder `@@lynis-rc=<n>` mit `@@report` bzw. `@@stale`."""
    dt.paths(run_id, base_dir, kind=AUDITS)  # ValueError bei ungültiger ID oder ungültigem Ordner
    limit = int(limit_s)
    if limit < 1:
        raise ValueError("Ungültige Obergrenze.")
    d = _checked_path(base_dir)
    start = _checked_path(f"{base_dir}/{run_id}.start")
    lock = _checked_path(f"{base_dir}/lynis.lock")
    rep = _checked_path(report)
    pids = " ".join(_checked_path(p) for p in pid_files)
    return (
        # /usr/sbin ist nicht überall im Suchpfad (sudo, systemd); sicherheitshalber ergänzen.
        'PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"; '
        "command -v lynis >/dev/null 2>&1 || { echo @@nolynis; exit 0; }; "
        # Lynis löscht beim Start ein `./lynis.pid` im aktuellen Ordner: nur im eigenen Ordner laufen.
        f"cd {d} || exit 1; "
        "L=; command -v flock >/dev/null 2>&1 && L=1; "
        "( "
        # Zwei Läufe würden in denselben Bericht schreiben. flock: Rückgabe 1 heißt "belegt", andere Fehler
        # (z. B. ein Dateisystem ohne Sperren) lassen den Lauf zu.
        'if [ -n "$L" ]; then flock -n 9; [ $? -ne 1 ] || { echo @@busy; exit 0; }; fi; '
        # Auch ein von Hand gestartetes Lynis zählt: Lebt der Prozess aus seiner PID-Datei noch, läuft ein Audit.
        f"for f in {pids}; do p=$(cat \"$f\" 2>/dev/null); case \"$p\" in ''|*[!0-9]*) continue;; esac; "
        "if [ -r \"/proc/$p/cmdline\" ] && tr '\\000' ' ' <\"/proc/$p/cmdline\" | grep -q 'lynis audit'; then "
        "echo @@busy; exit 0; fi; done; "
        f": >{start} || exit 1; "
        f'N=; command -v nice >/dev/null 2>&1 && N="nice -n {NICE}"; '
        # `ionice -c3` gibt es nicht überall oder es ist nicht erlaubt.
        'if command -v ionice >/dev/null 2>&1 && ionice -c3 true >/dev/null 2>&1; then N="$N ionice -c3"; fi; '
        # Nur `timeout` aus den coreutils, das `-k` kennt (wie `antivirus.guard_command`). Es läuft in einer eigenen
        # Prozessgruppe und beendet bei der Obergrenze Lynis samt Unterprozessen.
        "T=; case \"$(timeout --version 2>/dev/null)\" in *coreutils*) "
        f"timeout -k 1 5 true >/dev/null 2>&1 && T=\"timeout -k {KILL_AFTER_S} {limit}\";; esac; "
        'echo "@@prio=${N:-normal}"; '
        "S=$(date +%s 2>/dev/null); $T $N lynis audit system --quick --quiet --no-colors </dev/null; R=$?; "
        'if [ -n "$T" ] && [ "$R" -eq 124 ]; then echo @@timeout; exit 0; fi; '
        # 137 (KILL) kommt von `timeout` erst nach der Obergrenze; vorher hat jemand anderes Lynis beendet, meist der
        # Kernel bei Speichermangel. Ohne Uhrzeit gilt es als "zu lange".
        'if [ "$R" -eq 137 ]; then E=$(date +%s 2>/dev/null); '
        f'if [ -n "$T" ] && {{ [ -z "$S" ] || [ -z "$E" ] || [ $((E - S)) -ge {limit} ]; }}; then echo @@timeout; '
        "else echo @@killed; fi; exit 0; fi; "
        'echo "@@lynis-rc=$R"; '
        # Nur ein Bericht, der neuer ist als der Start dieses Laufs, zählt; ein alter nie.
        f"if [ -f {rep} ] && [ ! -L {rep} ] && [ {rep} -nt {start} ]; then echo @@report; "
        f"grep -E '^(warning\\[\\]|suggestion\\[\\]|hardening_index|finish)=' {rep}; else echo @@stale; fi"
        f" ) 9>>{lock}"
    )


def poll_command(run_id: str, *, base_dir: str = AUDIT_DIR) -> str:
    """Stand eines Laufs (als root) im Format von `detached.parse_poll`: `@@running`, `@@rc=<n>`, `@@lost` oder `@@unknown`."""
    dt.paths(run_id, base_dir, kind=AUDITS)
    d = _checked_path(base_dir)
    alive = dt.running_test(run_id, base_dir, kind=AUDITS)
    return (
        f"if {alive}; then echo @@running; echo @@tail; "
        f"elif [ -f {d}/{run_id}.rc ]; then echo \"@@rc=$(cat {d}/{run_id}.rc)\"; echo @@tail; "
        f"tail -c {TAIL_BYTES} {d}/{run_id}.log 2>/dev/null; "
        f"elif [ -n \"$p\" ]; then echo @@lost; echo @@tail; tail -n 20 {d}/{run_id}.log 2>/dev/null; "
        "else echo @@unknown; echo @@tail; fi"
    )


def stop_command(run_id: str, *, base_dir: str = AUDIT_DIR) -> str:
    """Beendet einen Lauf über der Obergrenze samt Lynis (als root); Ausgabe `@@stopped`, `@@stuck` oder `@@notrunning`."""
    dt.paths(run_id, base_dir, kind=AUDITS)
    unit = f"{AUDITS.unit_prefix}{run_id}.service"
    alive = dt.running_test(run_id, base_dir, kind=AUDITS)
    # Der Wrapper führt eine eigene Session (`detached.launch_command`): `pkill -s` trifft alles darin, auch `timeout`
    # (eigene Gruppe, gleiche Session). Erst TERM, damit Lynis seine PID-Datei wegräumt, nach 10 Sekunden KILL.
    return (
        f"if {alive}; then "
        f"systemctl stop --no-block {unit} >/dev/null 2>&1; "
        # Gruppe als `kill -TERM -<nr>` ohne `--`: das `kill` von dash (Debian, Ubuntu) bricht sonst mit
        # „Illegal number“ ab, und ohne `pkill` (procps fehlt) liefe Lynis weiter.
        'pkill -TERM -s "$p" 2>/dev/null; kill -TERM "-$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null; '
        'i=0; while [ $i -lt 10 ] && kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i+1)); done; '
        'pkill -KILL -s "$p" 2>/dev/null; kill -KILL "-$p" 2>/dev/null; kill -KILL "$p" 2>/dev/null; sleep 1; '
        f"if {alive} || pgrep -s \"$p\" >/dev/null 2>&1; then echo @@stuck; else echo @@stopped; fi; "
        "else echo @@notrunning; fi"
    )


def parse_result(output: str) -> av.LynisResult:
    """Auswertung des Protokolls eines fertigen Laufs (`lynis_command`)."""
    lines = [ln.strip() for ln in output.splitlines()]
    if "@@nolynis" in lines:
        return av.LynisResult(status="error", error=NOT_INSTALLED_MESSAGE)
    if "@@busy" in lines:
        return av.LynisResult(status="error", error=BUSY_MESSAGE)
    if "@@timeout" in lines:
        return av.LynisResult(status="error", error=too_long_message())
    if "@@killed" in lines:
        return av.LynisResult(status="error", error=KILLED_MESSAGE)
    rc = next((int(m.group(1)) for ln in lines if (m := _LYNIS_RC_RE.match(ln))), None)
    if "@@stale" in lines:
        return av.LynisResult(status="error", error=stale_message(rc))
    if "@@report" not in lines:
        return av.LynisResult(status="error", error=UNREADABLE_MESSAGE)
    section = lines[len(lines) - 1 - lines[::-1].index("@@report") + 1:]
    # Erst am Ende schreibt Lynis den Index und `finish=true`: fehlt beides, wurde es mittendrin beendet.
    if not any(ln == "finish=true" or ln.startswith("hardening_index=") for ln in section):
        return av.LynisResult(status="error", error=INCOMPLETE_MESSAGE)
    return av.parse_lynis("\n".join(section))
