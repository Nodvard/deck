"""Image-Update-Laeufe entkoppelt vom SSH-Kanal starten und abfragen.

Ein `docker compose pull` kann Minuten dauern, und beim Neuerstellen eines Containers
(oder dem Update eines Docker-Dienstes, der den Container von Nodvard Deck beendet) kann der
SSH-Kanal des Dashboards abreissen. Damit der Lauf dann nicht mitten drin mit SIGPIPE
endet, startet ein kurzer SSH-Aufruf ihn auf dem Host selbst (`nohup setsid`, ohne jede
Verbindung zu stdin/stdout/stderr des Kanals) und kehrt sofort zurueck. Die Ausgabe
landet in `<JOB_DIR>/<run_id>.log`, der Rueckgabecode in `<run_id>.rc`, die Prozess-ID
in `<run_id>.pid`. Das Dashboard fragt danach alle paar Sekunden kurz nach (`poll_command`).

Aufbau wie `nodvard_deck_ext_nexus_soc/detached.py` -- bewusst KEIN Import von dort: keine
Extension importiert eine andere (nexus-soc kann ausgeschaltet sein), und die dortige
Fassung ist auf root, systemd-run und apt/dpkg zugeschnitten. Hier: ohne root, im
Home-Ordner des SSH-Benutzers, nur setsid. Spaeter besser gemeinsam im SDK.

Nur die REINEN Teile: Befehle bauen und Ausgaben auswerten.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

JOB_DIR_SH = '"$HOME"/.local/state/lattice-image-updates'
"""Eine feste Shell-Angabe (kein Nutzertext): der Ordner der Protokolle im Home des SSH-Benutzers."""
JOB_DIR_DISPLAY = "~/.local/state/lattice-image-updates"
KEEP_DAYS = 30
RUN_PREFIX = "imgupd_"

RUN_ID_RE = re.compile(r"^imgupd_[0-9a-f]{16}\Z")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")
"""Composes Regel fuer Projektnamen -- der Name der Sperre auf dem Host (`lock-<projekt>`)."""
_PID_RE = re.compile(r"^@@pid=(\d+)$", re.MULTILINE)
_METHOD_RE = re.compile(r"^@@method=([a-z-]+)$", re.MULTILINE)
_RC_RE = re.compile(r"^@@rc=(-?\d*)$")
_STEP_RE = re.compile(r"^@@step=([a-z]+)$")

TAIL_BYTES = 8000


def _check(run_id: str) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError("Ungültige Lauf-ID.")


def _check_lock(lock: str | None) -> None:
    if lock is not None and (not isinstance(lock, str) or not PROJECT_RE.match(lock)):
        raise ValueError("Ungültiger Projektname für die Sperre.")


_ALIVE_SH = (
    '[ -n "$p" ] && [ -r "/proc/$p/cmdline" ] && tr \'\\000\' \' \' <"/proc/$p/cmdline" | grep -q ' + RUN_PREFIX
)
"""Shell-Bedingung: laeuft der Prozess `$p` noch, und ist es ein Lauf von uns (sein Aufruf enthaelt
`imgupd_`)? Ein wiederverwendete Prozessnummer eines fremden Programms zaehlt nicht."""


def busy_check_command(project: str) -> str:
    """Nur lesend: `@@busy`, wenn auf dem Host schon ein Update dieses Compose-Projekts laeuft
    (Sperre `lock-<projekt>` mit lebendem Lauf)."""
    _check_lock(project)
    return f'd={JOB_DIR_SH}; lk="$d/lock-{project}"; p=$(cat "$lk/pid" 2>/dev/null); if [ -d "$lk" ] && {_ALIVE_SH}; then echo @@busy; fi'


def log_path(run_id: str) -> str:
    """Wo das Protokoll auf dem Host liegt (nur zur Anzeige)."""
    _check(run_id)
    return f"{JOB_DIR_DISPLAY}/{run_id}.log"


def wrapper_command(run_id: str, script: str, lock: str | None = None) -> str:
    """Das, was auf dem Host entkoppelt laeuft: pid merken, `script` mit Ausgabe ins
    Protokoll, danach den Rueckgabecode atomar (erst .tmp, dann mv) ablegen. Die Subshell
    um das Skript ist noetig, weil es mit `exit N` enden kann -- sonst kaeme der Wrapper
    nie bis zur rc-Datei.

    Kein systemd-run, deshalb ist das `$$`-Verdoppeln aus nexus-soc hier nicht
    noetig: die Shell selbst wertet `$$` aus.

    Mit `lock` (Projektname): die Sperre `lock-<projekt>` (vom Start angelegt) bekommt zuerst
    die Prozessnummer dieses Laufs -- ERST danach erscheint die pid-Datei, auf die der Start
    wartet -- und wird ganz am Ende (nach der rc-Datei) wieder entfernt."""
    _check(run_id)
    _check_lock(lock)
    take = f'lk="$d/lock-{lock}"; echo $$ >"$lk/pid" 2>/dev/null; ' if lock else ""
    drop = '; [ "$(cat "$lk/pid" 2>/dev/null)" = "$$" ] && rm -rf "$lk"' if lock else ""
    return (
        f"d={JOB_DIR_SH}; {take}"
        f'echo $$ >"$d/{run_id}.pid.tmp" && mv -f "$d/{run_id}.pid.tmp" "$d/{run_id}.pid"; '
        f'( {script} ) </dev/null >"$d/{run_id}.log" 2>&1; '
        f'echo $? >"$d/{run_id}.rc.tmp"; mv -f "$d/{run_id}.rc.tmp" "$d/{run_id}.rc"{drop}'
    )


def launch_command(run_id: str, script: str, lock: str | None = None) -> str:
    """Startet `script` entkoppelt und kehrt nach hoechstens ~5 s zurueck. Ausgabe:
    `@@launch`, `@@method=...`, dann `@@pid=<n>` (laeuft) oder `@@nopid` (nicht gestartet).
    `@@exists`: dieser Lauf wurde schon einmal gestartet -- es wird kein zweiter gestartet.
    `@@busy` (mit `lock`): auf dem Host laeuft schon ein Update dieses Compose-Projekts -- nichts
    wird gestartet. Die Sperre ist ein Ordner `lock-<projekt>` (`mkdir` ist atomar); gehoert sie
    keinem lebenden Lauf mehr (Absturz, Neustart des Hosts), wird sie aufgeraeumt.

    Der Hintergrund-Lauf erbt keinen Datei-Deskriptor des SSH-Kanals (stdin/stdout/stderr
    umgeleitet), sonst wartete der SSH-Aufruf auf ihn."""
    _check(run_id)
    w = shlex.quote(wrapper_command(run_id, script, lock))
    rid = run_id  # durch RUN_ID_RE gesichert: nur Buchstaben, Ziffern, "_"
    acquire = (
        f'lk="$d/lock-{lock}"; '
        f'if ! mkdir "$lk" 2>/dev/null; then p=$(cat "$lk/pid" 2>/dev/null); '
        f"if {_ALIVE_SH}; then echo @@busy; exit 0; fi; "
        f'rm -rf "$lk"; mkdir "$lk" 2>/dev/null || {{ echo @@nopid; exit 1; }}; fi; echo $$ >"$lk/pid"; '
        if lock else ""
    )
    return (
        f'echo @@launch; d={JOB_DIR_SH}; mkdir -p -m 700 "$d" || {{ echo @@nopid; exit 1; }}; '
        # Alte Protokolle aufraeumen (nur eigene Dateien, nur aelter als KEEP_DAYS Tage).
        f"find \"$d\" -maxdepth 1 -type f -name '{RUN_PREFIX}*' -mtime +{KEEP_DAYS} -exec rm -f {{}} + 2>/dev/null; "
        f'if [ -e "$d/{rid}.pid" ]; then echo @@exists; echo "@@pid=$(cat "$d/{rid}.pid")"; exit 0; fi; '
        f"{acquire}"
        f"if command -v setsid >/dev/null 2>&1; then nohup setsid /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=setsid; "
        f"else nohup /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=nohup; fi; "
        # Bis zu 5 s auf die pid-Datei warten (ohne "sleep 0.1" eben laenger).
        f'i=0; while [ ! -s "$d/{rid}.pid" ] && [ $i -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; i=$((i+1)); done; '
        f'if [ -s "$d/{rid}.pid" ]; then echo "@@pid=$(cat "$d/{rid}.pid")"; else echo @@nopid; fi'
    )


def poll_command(run_id: str) -> str:
    """Fragt den Stand eines Laufs ab. Reihenfolge wichtig: erst "laeuft der Prozess noch?",
    dann "gibt es die rc-Datei?" -- andersherum koennte ein Lauf, der genau dazwischen
    fertig wird, faelschlich als verloren gelten (die rc-Datei wird vor dem Ende des
    Prozesses geschrieben).

    Ausgabe: `@@running` / `@@rc=<n>` / `@@lost` / `@@unknown`, darunter (falls schon
    vorhanden) `@@step=<schritt>`, dann `@@tail` und das Ende des Protokolls."""
    _check(run_id)
    rid = run_id
    step = f"grep '^@@step=' \"$d/{rid}.log\" 2>/dev/null | tail -n 1"
    return (
        f'd={JOB_DIR_SH}; p=$(cat "$d/{rid}.pid" 2>/dev/null); '
        f'if [ -n "$p" ] && [ -r "/proc/$p/cmdline" ] && tr \'\\000\' \' \' <"/proc/$p/cmdline" | grep -q {rid}; then '
        f'echo @@running; {step}; echo @@tail; tail -n 1 "$d/{rid}.log" 2>/dev/null; '
        f'elif [ -f "$d/{rid}.rc" ]; then echo "@@rc=$(cat "$d/{rid}.rc")"; {step}; '
        f'echo @@tail; tail -c {TAIL_BYTES} "$d/{rid}.log" 2>/dev/null; '
        f'elif [ -n "$p" ]; then echo @@lost; {step}; echo @@tail; tail -n 30 "$d/{rid}.log" 2>/dev/null; '
        f"else echo @@unknown; echo @@tail; fi"
    )


@dataclass
class Launch:
    method: str | None
    pid: int | None
    exists: bool = False
    busy: bool = False

    @property
    def started(self) -> bool:
        return self.pid is not None


def parse_launch(output: str) -> Launch:
    m = _PID_RE.search(output)
    method = _METHOD_RE.search(output)
    return Launch(method=method.group(1) if method else None, pid=int(m.group(1)) if m else None,
                  exists="@@exists" in output.splitlines(), busy="@@busy" in output.splitlines())


@dataclass
class Poll:
    state: str  # running | done | lost | unknown (Host kennt den Lauf nicht) | noreply (gar keine Antwort)
    rc: int | None = None
    output: str = ""  # Protokoll-Ende
    progress: str | None = None  # letzte Protokollzeile, solange der Lauf laeuft
    step: str | None = None  # tag | pull | up | done (letzte `@@step=`-Zeile des Skripts)


def parse_poll(output: str) -> Poll:
    head, sep, tail = output.partition("\n@@tail\n")
    if not sep and head.rstrip().endswith("@@tail"):
        head, tail = head.rstrip()[: -len("@@tail")], ""
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    step = None
    for line in lines[1:]:
        m = _STEP_RE.match(line)
        if m:
            step = m.group(1)
    if first == "@@running":
        last = tail.strip().splitlines()[-1].strip() if tail.strip() else None
        return Poll(state="running", progress=last[:200] if last else None, step=step)
    m = _RC_RE.match(first)
    if m:
        return Poll(state="done", rc=int(m.group(1)) if m.group(1) not in ("", "-") else None, output=tail, step=step)
    if first == "@@lost":
        return Poll(state="lost", output=tail.strip(), step=step)
    if not first.startswith("@@"):
        # Leere oder fremde Antwort (Kanal mitten im Aufruf verloren): sagt nichts ueber den
        # Lauf aus -- nur der Host selbst antwortet mit "@@unknown".
        return Poll(state="noreply")
    return Poll(state="unknown")
