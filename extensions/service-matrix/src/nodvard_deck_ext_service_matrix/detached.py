"""Image-Update-Läufe entkoppelt vom SSH-Kanal starten und abfragen (nur Befehle bauen und Ausgaben auswerten)."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# `docker compose pull` dauert Minuten, und erstellt es den Container von Nodvard Deck neu, reißt der SSH-Kanal ab. Der Lauf
# startet daher auf dem Host (`nohup setsid`, ohne stdin/stdout/stderr des Kanals, sonst SIGPIPE) und schreibt Protokoll,
# Rückgabecode und PID nach `<JOB_DIR>/<run_id>.log/.rc/.pid`. Eigene Fassung ohne root und systemd-run, nicht aus Shield.
JOB_DIR_SH = '"$HOME"/.local/state/lattice-image-updates'
"""Eine feste Shell-Angabe (kein Nutzertext): der Ordner der Protokolle im Home des SSH-Benutzers."""
JOB_DIR_DISPLAY = "~/.local/state/lattice-image-updates"
KEEP_DAYS = 30
RUN_PREFIX = "imgupd_"

RUN_ID_RE = re.compile(r"^imgupd_[0-9a-f]{16}\Z")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")
"""Composes Regel für Projektnamen; der Name der Sperre auf dem Host (`lock-<projekt>`)."""
_PID_RE = re.compile(r"^@@pid=(\d+)$", re.MULTILINE)
_METHOD_RE = re.compile(r"^@@method=([a-z-]+)$", re.MULTILINE)
_RC_RE = re.compile(r"^@@rc=(-?\d*)$")
_STEP_RE = re.compile(r"^@@step=([a-z]+)$")

TAIL_BYTES = 8000


# Lauf-ID und Sperrname landen in Shell-Befehlen: nur die feste Form kommt durch.
def _check(run_id: str) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ValueError("Ungültige Lauf-ID.")


def _check_lock(lock: str | None) -> None:
    if lock is not None and (not isinstance(lock, str) or not PROJECT_RE.match(lock)):
        raise ValueError("Ungültiger Projektname für die Sperre.")


_ALIVE_SH = (
    '[ -n "$p" ] && [ -r "/proc/$p/cmdline" ] && tr \'\\000\' \' \' <"/proc/$p/cmdline" | grep -q ' + RUN_PREFIX
)
"""Shell-Bedingung: läuft `$p` noch, und ist es ein Lauf von uns (Aufruf enthält `imgupd_`)?
So zählt eine wiederverwendete Prozessnummer eines fremden Programms nicht."""


def busy_check_command(project: str) -> str:
    """Nur lesend: `@@busy`, wenn auf dem Host schon ein Update dieses Compose-Projekts läuft (Sperre `lock-<projekt>`)."""
    _check_lock(project)
    return f'd={JOB_DIR_SH}; lk="$d/lock-{project}"; p=$(cat "$lk/pid" 2>/dev/null); if [ -d "$lk" ] && {_ALIVE_SH}; then echo @@busy; fi'


def log_path(run_id: str) -> str:
    """Wo das Protokoll auf dem Host liegt (nur zur Anzeige)."""
    _check(run_id)
    return f"{JOB_DIR_DISPLAY}/{run_id}.log"


def wrapper_command(run_id: str, script: str, lock: str | None = None) -> str:
    """Was auf dem Host entkoppelt läuft: pid merken, `script` mit Ausgabe ins Protokoll, danach den Rückgabecode ablegen.

    Mit `lock` (Projektname) bekommt die Sperre `lock-<projekt>` zuerst die Prozessnummer dieses Laufs.
    """
    _check(run_id)
    _check_lock(lock)
    # Kein systemd-run, die Shell wertet `$$` selbst aus (kein Verdoppeln nötig). Die Sperre trägt die Prozessnummer, ehe die
    # pid-Datei erscheint, auf die der Start wartet, und fällt ganz am Ende (nach der rc-Datei) weg.
    take = f'lk="$d/lock-{lock}"; echo $$ >"$lk/pid" 2>/dev/null; ' if lock else ""
    drop = '; [ "$(cat "$lk/pid" 2>/dev/null)" = "$$" ] && rm -rf "$lk"' if lock else ""
    # Rückgabecode atomar ablegen (erst .tmp, dann mv). Die Subshell um das Skript ist nötig, weil es mit `exit N` enden
    # kann: sonst käme der Wrapper nie bis zur rc-Datei.
    return (
        f"d={JOB_DIR_SH}; {take}"
        f'echo $$ >"$d/{run_id}.pid.tmp" && mv -f "$d/{run_id}.pid.tmp" "$d/{run_id}.pid"; '
        f'( {script} ) </dev/null >"$d/{run_id}.log" 2>&1; '
        f'echo $? >"$d/{run_id}.rc.tmp"; mv -f "$d/{run_id}.rc.tmp" "$d/{run_id}.rc"{drop}'
    )


def launch_command(run_id: str, script: str, lock: str | None = None) -> str:
    """Startet `script` entkoppelt, kehrt nach höchstens ~5 s zurück und meldet `@@pid=<n>` (läuft) oder `@@nopid`.

    `@@exists`: dieser Lauf wurde schon gestartet. `@@busy` (mit `lock`): für das Compose-Projekt läuft schon ein Update.
    """
    _check(run_id)
    w = shlex.quote(wrapper_command(run_id, script, lock))
    rid = run_id  # durch RUN_ID_RE gesichert: nur Buchstaben, Ziffern, "_"
    # Die Sperre ist ein Ordner (`mkdir` ist atomar); gehört sie keinem lebenden Lauf mehr (Absturz, Neustart des Hosts),
    # wird sie aufgeräumt.
    acquire = (
        f'lk="$d/lock-{lock}"; '
        f'if ! mkdir "$lk" 2>/dev/null; then p=$(cat "$lk/pid" 2>/dev/null); '
        f"if {_ALIVE_SH}; then echo @@busy; exit 0; fi; "
        f'rm -rf "$lk"; mkdir "$lk" 2>/dev/null || {{ echo @@nopid; exit 1; }}; fi; echo $$ >"$lk/pid"; '
        if lock else ""
    )
    return (
        f'echo @@launch; d={JOB_DIR_SH}; mkdir -p -m 700 "$d" || {{ echo @@nopid; exit 1; }}; '
        # Alte Protokolle aufräumen (nur eigene Dateien, nur älter als KEEP_DAYS Tage).
        f"find \"$d\" -maxdepth 1 -type f -name '{RUN_PREFIX}*' -mtime +{KEEP_DAYS} -exec rm -f {{}} + 2>/dev/null; "
        f'if [ -e "$d/{rid}.pid" ]; then echo @@exists; echo "@@pid=$(cat "$d/{rid}.pid")"; exit 0; fi; '
        f"{acquire}"
        # stdin/stdout/stderr umgeleitet: erbte der Lauf einen Deskriptor des SSH-Kanals, wartete der SSH-Aufruf auf ihn.
        f"if command -v setsid >/dev/null 2>&1; then nohup setsid /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=setsid; "
        f"else nohup /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=nohup; fi; "
        # Bis zu 5 s auf die pid-Datei warten (ohne "sleep 0.1" eben länger).
        f'i=0; while [ ! -s "$d/{rid}.pid" ] && [ $i -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; i=$((i+1)); done; '
        f'if [ -s "$d/{rid}.pid" ]; then echo "@@pid=$(cat "$d/{rid}.pid")"; else echo @@nopid; fi'
    )


def poll_command(run_id: str) -> str:
    """Fragt den Stand eines Laufs ab.

    Ausgabe: `@@running` / `@@rc=<n>` / `@@lost` / `@@unknown`, dann `@@step=<schritt>`, `@@tail` und das Protokollende.
    """
    _check(run_id)
    rid = run_id
    # Erst "läuft der Prozess noch?", dann "gibt es die rc-Datei?": andersherum würde ein Lauf, der genau dazwischen fertig
    # wird, fälschlich als verloren gelten (die rc-Datei entsteht vor dem Ende des Prozesses).
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
    progress: str | None = None  # letzte Protokollzeile, solange der Lauf läuft
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
        # Leere oder fremde Antwort (Kanal mitten im Aufruf verloren): sagt nichts über den Lauf aus, nur der Host selbst
        # antwortet mit "@@unknown".
        return Poll(state="noreply")
    return Poll(state="unknown")
