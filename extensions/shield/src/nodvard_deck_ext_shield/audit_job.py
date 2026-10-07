"""Haertungs-Audit (Lynis) entkoppelt vom SSH-Kanal: Befehle bauen und Ausgaben auswerten.

Vorher lief `lynis audit system` direkt im SSH-Kanal, mit 30 Minuten Zeitlimit. Auf einem Server mit vielen Benutzern und
Containern braucht Lynis aber gut eine Stunde: Das Dashboard gab nach 30 Minuten auf, Lynis lief auf dem Server ohne
Zeitlimit weiter, und den Bericht, den es am Ende schrieb, las niemand mehr.

Jetzt startet ein kurzer SSH-Aufruf den Lauf auf dem Server (`detached.launch_command` mit der Art `AUDITS`: eigener Ordner
`AUDIT_DIR`, systemd-Unit `nodvard-shield-audit-<lauf>` bzw. `setsid`), und das Dashboard fragt nur kurz nach
(`poll_command`), auch nach einem eigenen Neustart. Auf dem Server (`lynis_command`):

- Lynis laeuft mit niedriger Prioritaet: `nice -n 19`, dazu `ionice -c3`, wenn es das gibt und es sich setzen laesst.
- `timeout` aus den coreutils beendet Lynis samt allen Unterprozessen nach `LIMIT_S` (TERM, eine Minute spaeter KILL).
  Fehlt `timeout`, beendet das Dashboard den Lauf nach der Obergrenze selbst (`stop_command`).
- Es startet kein zweites Lynis, solange eines laeuft: Sperre im eigenen Ordner (`flock`, wenn vorhanden) und die
  PID-Datei von Lynis selbst. Auch ein von Hand oder von einer frueheren Version gestartetes Lynis zaehlt -- zwei
  gleichzeitige Laeufe schreiben in denselben Bericht.
- Direkt nach dem Ende wird `/var/log/lynis-report.dat` gelesen, aber nur, wenn der Bericht neuer ist als der Start
  dieses Laufs (`<lauf>.start`): Ein alter Bericht gilt nie als Ergebnis. Die Zeilen stehen danach im Protokoll des
  Laufs (`<lauf>.log`) und kommen auch nach einem Neustart des Dashboards noch an.

Nur die REINEN Teile (wie `detached.py`); Ausfuehren, Speichern und Melden steht in `defender.py`.
"""

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
# Obergrenze eines Laufs auf dem Server. Grosszuegig: Auf einem Docker-Host mit vielen Benutzern dauert Lynis gut eine
# Stunde, mit niedriger Prioritaet auf einem ausgelasteten Server auch laenger.
LIMIT_S = 3 * 60 * 60
KILL_AFTER_S = 60  # so lange nach dem TERM bei der Obergrenze folgt KILL
NICE = 19
# So viel vom Ende des Protokolls holt die Abfrage eines fertigen Laufs (der Bericht steht ganz am Ende).
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
    """Der Befehl, der auf dem Server entkoppelt laeuft (als root, im Wrapper von `detached.launch_command`, die
    Ausgabe landet in `<lauf>.log`). Zeilen fuer `parse_result`, je Lauf genau eine davon:

    - `@@nolynis`: Lynis ist nicht installiert.
    - `@@busy`: Es laeuft schon ein Lynis (Sperre belegt oder PID-Datei von Lynis mit lebendem Prozess).
    - `@@timeout`: `timeout` hat Lynis nach `limit_s` beendet.
    - `@@lynis-rc=<n>`, dann `@@report` und die Zeilen des frischen Berichts, oder `@@stale` (kein neuer Bericht).

    Davor steht `@@prio=...` (womit Lynis gestartet wurde) und, was Lynis selbst ausgibt. `base_dir`, `report` und
    `pid_files` sind nur fuer Tests anders."""
    dt.paths(run_id, base_dir, kind=AUDITS)  # ValueError bei ungueltiger ID oder ungueltigem Ordner
    limit = int(limit_s)
    if limit < 1:
        raise ValueError("Ungültige Obergrenze.")
    d = _checked_path(base_dir)
    start = _checked_path(f"{base_dir}/{run_id}.start")
    lock = _checked_path(f"{base_dir}/lynis.lock")
    rep = _checked_path(report)
    pids = " ".join(_checked_path(p) for p in pid_files)
    return (
        # Als root ist /usr/sbin meist im Suchpfad, ueber `sudo` (secure_path) und systemd auch; sicher ist sicher.
        'PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"; '
        "command -v lynis >/dev/null 2>&1 || { echo @@nolynis; exit 0; }; "
        # Lynis loescht beim Start ein `./lynis.pid` im aktuellen Ordner: nur im eigenen Ordner laufen.
        f"cd {d} || exit 1; "
        "L=; command -v flock >/dev/null 2>&1 && L=1; "
        "( "
        # Sperre: Rueckgabe 1 heisst "belegt"; jeder andere Fehler (z. B. ein Dateisystem ohne Sperren) laesst den Lauf zu.
        'if [ -n "$L" ]; then flock -n 9; [ $? -ne 1 ] || { echo @@busy; exit 0; }; fi; '
        # Lynis selbst: lebt der Prozess aus seiner PID-Datei noch, laeuft gerade ein Audit (sonst ist die Datei ein Rest).
        f"for f in {pids}; do p=$(cat \"$f\" 2>/dev/null); case \"$p\" in ''|*[!0-9]*) continue;; esac; "
        "if [ -r \"/proc/$p/cmdline\" ] && tr '\\000' ' ' <\"/proc/$p/cmdline\" | grep -q 'lynis audit'; then "
        "echo @@busy; exit 0; fi; done; "
        f": >{start} || exit 1; "
        f'N=; command -v nice >/dev/null 2>&1 && N="nice -n {NICE}"; '
        # `ionice -c3` (nur lesen und schreiben, wenn sonst niemand will): nicht ueberall vorhanden oder erlaubt.
        'if command -v ionice >/dev/null 2>&1 && ionice -c3 true >/dev/null 2>&1; then N="$N ionice -c3"; fi; '
        # Nur `timeout` aus den coreutils, das `-k` kennt (wie `antivirus.guard_command`). Es setzt sich in eine eigene
        # Prozessgruppe und beendet bei der Obergrenze die ganze Gruppe: Lynis und alles, was es gerade aufgerufen hat.
        "T=; case \"$(timeout --version 2>/dev/null)\" in *coreutils*) "
        f"timeout -k 1 5 true >/dev/null 2>&1 && T=\"timeout -k {KILL_AFTER_S} {limit}\";; esac; "
        'echo "@@prio=${N:-normal}"; '
        "$T $N lynis audit system --quick --quiet --no-colors </dev/null; R=$?; "
        'if [ -n "$T" ] && { [ "$R" -eq 124 ] || [ "$R" -eq 137 ]; }; then echo @@timeout; exit 0; fi; '
        'echo "@@lynis-rc=$R"; '
        f"if [ -f {rep} ] && [ ! -L {rep} ] && [ {rep} -nt {start} ]; then echo @@report; "
        f"grep -E '^(warning\\[\\]|suggestion\\[\\]|hardening_index|finish)=' {rep}; else echo @@stale; fi"
        f" ) 9>>{lock}"
    )


def poll_command(run_id: str, *, base_dir: str = AUDIT_DIR) -> str:
    """Stand eines Laufs (als root), im Format von `detached.parse_poll`: `@@running`, `@@rc=<n>` samt Ende des
    Protokolls, `@@lost` (Prozess weg ohne Rueckgabecode) oder `@@unknown` (der Server kennt den Lauf nicht)."""
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
    """Beendet einen Lauf, der die Obergrenze ueberschritten hat (als root), samt Lynis und allen Unterprozessen.

    Der Wrapper ist Anfuehrer einer eigenen Sitzung (`detached.launch_command`, systemd und `setsid`): beendet wird alles
    mit dieser Sitzungsnummer (`pkill -s`), dazu seine Prozessgruppe und er selbst. `timeout` laeuft in einer eigenen
    Gruppe, aber in derselben Sitzung. Erst TERM (Lynis raeumt dabei seine PID-Datei weg), nach bis zu 10 Sekunden KILL.
    Ausgabe: `@@stopped`, `@@stuck` (es laeuft danach noch etwas) oder `@@notrunning` (der Lauf war schon zu Ende)."""
    dt.paths(run_id, base_dir, kind=AUDITS)
    unit = f"{AUDITS.unit_prefix}{run_id}.service"
    alive = dt.running_test(run_id, base_dir, kind=AUDITS)
    return (
        f"if {alive}; then "
        f"systemctl stop --no-block {unit} >/dev/null 2>&1; "
        'pkill -TERM -s "$p" 2>/dev/null; kill -TERM -- "-$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null; '
        'i=0; while [ $i -lt 10 ] && kill -0 "$p" 2>/dev/null; do sleep 1; i=$((i+1)); done; '
        'pkill -KILL -s "$p" 2>/dev/null; kill -KILL -- "-$p" 2>/dev/null; kill -KILL "$p" 2>/dev/null; sleep 1; '
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
