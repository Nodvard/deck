"""Einspiel-Laeufe entkoppelt vom SSH-Kanal starten und abfragen.

Vorher hingen apt-get und dpkg bis zu 45 Minuten direkt am SSH-Kanal des Dashboards.
Riss der Kanal ab -- etwa weil ein Docker-Update auf dem Raspberry Pi dockerd neu startet
und damit den Nodvard-Deck-Container beendet --, bekam apt beim naechsten Schreiben
SIGPIPE, und dpkg blieb halb konfiguriert zurueck ("dpkg was interrupted").

Jetzt startet ein kurzer SSH-Aufruf den Lauf auf dem Server selbst und kehrt sofort
zurueck:
- mit systemd als eigene Unit `lattice-upgrade-<run_id>` (`systemd-run --collect`),
- ohne systemd (oder wenn systemd-run scheitert) per `nohup setsid` in einer eigenen
  Sitzung, ohne jede Verbindung zu stdin/stdout/stderr des SSH-Kanals.
Die Ausgabe landet in `<JOB_DIR>/<run_id>.log`, der Rueckgabecode am Ende in
`<run_id>.rc`, die Prozess-ID in `<run_id>.pid`. Das Dashboard fragt danach alle
paar Sekunden kurz per SSH nach (`poll_command`) -- auch nach einem eigenen Neustart.

Nur die REINEN Teile: Befehle bauen und Ausgaben auswerten (wie `updates.py`).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

JOB_DIR = "/var/lib/nexus-updates"
KEEP_DAYS = 30
UNIT_PREFIX = "lattice-upgrade-"
SYSTEMD_MARKER = "/run/systemd/system"  # gibt es nur, wenn systemd das Init-System ist

_RUN_ID_RE = re.compile(r"^upd_[0-9a-f]{16}$")
_DIR_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
_PID_RE = re.compile(r"^@@pid=(\d+)$", re.MULTILINE)
_METHOD_RE = re.compile(r"^@@method=([a-z-]+)$", re.MULTILINE)
_RC_RE = re.compile(r"^@@rc=(-?\d*)$")

# apt haelt Pakete zurueck, deren Update andere Pakete ersetzen oder entfernen
# muesste. Der Abschnitt steht frueh im Protokoll und ist bei langen Laeufen nicht mehr im
# Ende (`tail -c`), darum zieht die Abfrage ihn in EINE Zeile `@@kept-back: a b c` (die
# Namen stehen eingerueckt unter der Ueberschrift; Englisch und Deutsch). Folgt gleich
# eine zweite Ueberschrift, beginnt sie eine neue Zeile.
KEPT_BACK_MARK = "@@kept-back:"
_KEPT_AWK = (
    r'/(have been kept back|zur.*ckgehalten).*:[ \t]*$/ { if (f) print ""; f = 1; printf "@@kept-back:"; next } '
    r'f && /^[ \t]+[^ \t]/ { $1 = $1; printf " %s", $0; next } '
    r'f { f = 0; print "" } '
    r'END { if (f) print "" }'
)


def _check(run_id: str, base_dir: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ValueError("Ungültige Lauf-ID.")
    if not isinstance(base_dir, str) or not _DIR_RE.match(base_dir) or "/../" in f"{base_dir}/":
        raise ValueError("Ungültiger Ordner für die Update-Protokolle.")


def paths(run_id: str, base_dir: str = JOB_DIR) -> dict[str, str]:
    """Die Dateien eines Laufs auf dem Server (ungequotet, nur zur Anzeige)."""
    _check(run_id, base_dir)
    return {ext: f"{base_dir}/{run_id}.{ext}" for ext in ("log", "rc", "pid")}


def wrapper_command(run_id: str, command: str, *, base_dir: str = JOB_DIR) -> str:
    """Das, was auf dem Server entkoppelt laeuft: pid merken, Befehl mit Ausgabe ins
    Protokoll, danach den Rueckgabecode atomar (erst .tmp, dann mv) ablegen. Die
    Subshell um den Befehl ist noetig, weil `upgrade_command` mit `exit $R` endet --
    sonst kaeme der Wrapper nie bis zur rc-Datei."""
    _check(run_id, base_dir)
    f = shlex.quote(f"{base_dir}/{run_id}")
    return (
        f"echo $$ >{f}.pid.tmp && mv -f {f}.pid.tmp {f}.pid; "
        f"( {command} ) </dev/null >{f}.log 2>&1; "
        f"echo $? >{f}.rc.tmp; mv -f {f}.rc.tmp {f}.rc"
    )


def launch_command(run_id: str, command: str, *, base_dir: str = JOB_DIR, systemd_marker: str = SYSTEMD_MARKER) -> str:
    """Startet `command` entkoppelt und kehrt nach hoechstens ~5 s zurueck. Muss als
    root laufen (`as_root`). Ausgabe: `@@launch`, `@@method=...`, dann `@@pid=<n>`
    (laeuft) oder `@@nopid` (nicht gestartet). `@@exists`: dieser Lauf wurde schon
    einmal gestartet -- es wird kein zweiter gestartet.

    Wichtig: Der Hintergrund-Lauf erbt keinen Datei-Deskriptor des SSH-Kanals
    (stdin/stdout/stderr umgeleitet), sonst wartete der SSH-Aufruf auf ihn."""
    _check(run_id, base_dir)
    _check(run_id, systemd_marker)
    w = shlex.quote(wrapper_command(run_id, command, base_dir=base_dir))
    # systemd ersetzt im Befehl von `systemd-run` `$NAME` durch Umgebungsvariablen und
    # macht aus `$$` ein `$` -- ohne Verdoppeln stuende "$" statt der Prozessnummer in
    # der pid-Datei und jeder Lauf galt nach ~10 s als abgebrochen.
    # `--expand-environment=no` kennt systemd 252 (Debian 12, Proxmox 8, Pi OS) noch nicht.
    ws = shlex.quote(wrapper_command(run_id, command, base_dir=base_dir).replace("$", "$$"))
    d = shlex.quote(base_dir)
    rid = run_id  # durch _RUN_ID_RE gesichert: nur Buchstaben, Ziffern, "_"
    unit = f"{UNIT_PREFIX}{rid}"
    setsid = f"nohup setsid /bin/sh -c {w} </dev/null >/dev/null 2>&1 &"
    return (
        f"echo @@launch; d={d}; mkdir -p -m 700 \"$d\" || {{ echo @@nopid; exit 1; }}; "
        # Alte Protokolle aufraeumen (nur eigene Dateien, nur aelter als KEEP_DAYS Tage).
        f"find \"$d\" -maxdepth 1 -type f -name 'upd_*' -mtime +{KEEP_DAYS} -exec rm -f {{}} + 2>/dev/null; "
        f"if [ -e \"$d/{rid}.pid\" ]; then echo @@exists; echo \"@@pid=$(cat \"$d/{rid}.pid\")\"; exit 0; fi; "
        f"if [ -d {shlex.quote(systemd_marker)} ] && command -v systemd-run >/dev/null 2>&1; then "
        f"if systemd-run --unit={unit} --collect --quiet --description='Nodvard Deck Update {rid}' "
        f"/bin/sh -c {ws} </dev/null 2>&1; then echo @@method=systemd; "
        f"else sleep 1; [ -s \"$d/{rid}.pid\" ] || {{ {setsid} echo @@method=setsid-fallback; }}; fi; "
        f"elif command -v setsid >/dev/null 2>&1; then {setsid} echo @@method=setsid; "
        f"else nohup /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=nohup; fi; "
        # Bis zu 5 s auf die pid-Datei warten (ohne "sleep 0.1" eben laenger).
        f"i=0; while [ ! -s \"$d/{rid}.pid\" ] && [ $i -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; i=$((i+1)); done; "
        f"if [ -s \"$d/{rid}.pid\" ]; then echo \"@@pid=$(cat \"$d/{rid}.pid\")\"; else echo @@nopid; fi"
    )


def poll_command(run_id: str, *, base_dir: str = JOB_DIR) -> str:
    """Fragt den Stand eines Laufs ab (als root). Reihenfolge wichtig: erst "laeuft der
    Prozess noch?", dann "gibt es die rc-Datei?" -- andersherum koennte ein Lauf, der
    genau dazwischen fertig wird, faelschlich als verloren gelten (die rc-Datei wird
    vor dem Ende des Prozesses geschrieben).

    Ausgabe: `@@running` / `@@rc=<n>` / `@@lost` / `@@unknown`, danach `@@tail` und
    das Ende des Protokolls. Bei `@@rc` stehen davor unter `@@summary` die
    apt-Zusammenfassung und Fehlerzeilen (die bei langen Laeufen nicht mehr im Ende
    des Protokolls stehen)."""
    _check(run_id, base_dir)
    rid = run_id
    d = shlex.quote(base_dir)
    return (
        f"d={d}; p=$(cat \"$d/{rid}.pid\" 2>/dev/null); "
        f"if [ -n \"$p\" ] && [ -r \"/proc/$p/cmdline\" ] && tr '\\000' ' ' <\"/proc/$p/cmdline\" | grep -q {rid}; then "
        f"echo @@running; echo @@tail; tail -n 1 \"$d/{rid}.log\" 2>/dev/null; "
        f"elif [ -f \"$d/{rid}.rc\" ]; then echo \"@@rc=$(cat \"$d/{rid}.rc\")\"; echo @@summary; "
        f"grep -E 'upgraded, .* newly installed|aktualisiert, .* neu installiert|^E:' \"$d/{rid}.log\" 2>/dev/null | tail -n 5; "
        f"awk '{_KEPT_AWK}' \"$d/{rid}.log\" 2>/dev/null | head -n 2; "
        f"echo @@tail; tail -c 20000 \"$d/{rid}.log\" 2>/dev/null; "
        f"elif [ -n \"$p\" ]; then echo @@lost; echo @@summary; dpkg --audit 2>/dev/null | head -n 5; "
        f"echo @@tail; tail -n 20 \"$d/{rid}.log\" 2>/dev/null; "
        f"else echo @@unknown; echo @@tail; fi"
    )


@dataclass
class Launch:
    method: str | None
    pid: int | None
    exists: bool = False

    @property
    def started(self) -> bool:
        return self.pid is not None


def parse_launch(output: str) -> Launch:
    m = _PID_RE.search(output)
    method = _METHOD_RE.search(output)
    return Launch(method=method.group(1) if method else None, pid=int(m.group(1)) if m else None,
                  exists="@@exists" in output.splitlines())


@dataclass
class Poll:
    state: str  # running | done | lost | unknown (Server kennt den Lauf nicht) | noreply (gar keine Antwort)
    rc: int | None = None
    output: str = ""  # Zusammenfassung + Protokoll-Ende
    progress: str | None = None  # letzte Protokollzeile, solange der Lauf laeuft


def parse_poll(output: str) -> Poll:
    head, sep, tail = output.partition("\n@@tail\n")
    if not sep and head.rstrip().endswith("@@tail"):
        head, tail = head.rstrip()[: -len("@@tail")], ""
    lines = [ln.strip() for ln in head.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    summary = "\n".join(lines[lines.index("@@summary") + 1:]) if "@@summary" in lines else ""
    if first == "@@running":
        last = tail.strip().splitlines()[-1].strip() if tail.strip() else None
        return Poll(state="running", progress=last[:200] if last else None)
    m = _RC_RE.match(first)
    if m:
        text = f"{summary}\n{tail}" if summary else tail
        return Poll(state="done", rc=int(m.group(1)) if m.group(1) not in ("", "-") else None, output=text)
    if first == "@@lost":
        text = tail + (f"\n{summary}" if summary else "")
        return Poll(state="lost", output=text.strip())
    if not first.startswith("@@"):
        # Leere oder fremde Antwort (Kanal mitten im Aufruf verloren): sagt nichts ueber den
        # Lauf aus -- nur der Server selbst antwortet mit "@@unknown".
        return Poll(state="noreply")
    return Poll(state="unknown")
