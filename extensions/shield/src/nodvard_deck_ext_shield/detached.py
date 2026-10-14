"""Einspiel-Läufe entkoppelt vom SSH-Kanal starten und abfragen (reiner Teil: Befehle bauen, Ausgaben auswerten).

Reißt der Kanal ab, bekäme apt sonst SIGPIPE und dpkg bliebe halb konfiguriert; `audit_job.py` nutzt denselben Start.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# Je Lauf liegen dort <run_id>.log (Ausgabe), .rc (Rückgabecode, erst am Ende) und .pid.
JOB_DIR = "/var/lib/nexus-updates"
KEEP_DAYS = 30
UNIT_PREFIX = "lattice-upgrade-"
SYSTEMD_MARKER = "/run/systemd/system"  # gibt es nur, wenn systemd das Init-System ist


@dataclass(frozen=True)
class JobKind:
    """Art der entkoppelten Läufe: Lauf-ID-Anfang (`<prefix>_<16 hex>`), Ordner und systemd-Unit auf dem Server.

    Feste Werte aus dem Code, nie aus Eingaben."""

    prefix: str
    base_dir: str
    unit_prefix: str
    label: str


UPDATES = JobKind(prefix="upd", base_dir=JOB_DIR, unit_prefix=UNIT_PREFIX, label="Nodvard Deck Update")

_PREFIX_RE = re.compile(r"^[a-z]{2,10}$")
_UNIT_PREFIX_RE = re.compile(r"^[a-z][a-z-]*-$")
_LABEL_RE = re.compile(r"^[A-Za-z ]+$")
_DIR_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")
_PID_RE = re.compile(r"^@@pid=(\d+)$", re.MULTILINE)
_METHOD_RE = re.compile(r"^@@method=([a-z-]+)$", re.MULTILINE)
_RC_RE = re.compile(r"^@@rc=(-?\d*)$")

# apt hält Pakete zurück, deren Update andere ersetzen oder entfernen müsste. Der Abschnitt steht früh im Protokoll
# und fehlt bei langen Läufen im Ende (`tail -c`); die Abfrage zieht ihn darum in eine Zeile `@@kept-back: a b c`.
# Die Namen stehen eingerückt unter der Überschrift (englisch oder deutsch), eine zweite Überschrift beginnt eine neue Zeile.
KEPT_BACK_MARK = "@@kept-back:"
_KEPT_AWK = (
    r'/(have been kept back|zur.*ckgehalten).*:[ \t]*$/ { if (f) print ""; f = 1; printf "@@kept-back:"; next } '
    r'f && /^[ \t]+[^ \t]/ { $1 = $1; printf " %s", $0; next } '
    r'f { f = 0; print "" } '
    r'END { if (f) print "" }'
)


def _check(run_id: str, base_dir: str, kind: JobKind = UPDATES) -> None:
    if not (_PREFIX_RE.match(kind.prefix) and _UNIT_PREFIX_RE.match(kind.unit_prefix) and _LABEL_RE.match(kind.label)):
        raise ValueError("Ungültige Art des Laufs.")
    if not isinstance(run_id, str) or not re.fullmatch(rf"{kind.prefix}_[0-9a-f]{{16}}", run_id):
        raise ValueError("Ungültige Lauf-ID.")
    if not isinstance(base_dir, str) or not _DIR_RE.match(base_dir) or "/../" in f"{base_dir}/":
        raise ValueError("Ungültiger Ordner für die Protokolle.")


def paths(run_id: str, base_dir: str | None = None, *, kind: JobKind = UPDATES) -> dict[str, str]:
    """Die Dateien eines Laufs auf dem Server (ungequotet, nur zur Anzeige)."""
    base_dir = kind.base_dir if base_dir is None else base_dir
    _check(run_id, base_dir, kind)
    return {ext: f"{base_dir}/{run_id}.{ext}" for ext in ("log", "rc", "pid")}


def running_test(run_id: str, base_dir: str, *, kind: JobKind = UPDATES) -> str:
    """Shell-Bedingung „der Lauf läuft noch“: die pid-Datei nennt einen Prozess, dessen Befehlszeile die Lauf-ID trägt.

    Setzt `p` auf die Nummer aus der pid-Datei (leer, wenn es keine gibt)."""
    _check(run_id, base_dir, kind)
    d = shlex.quote(base_dir)
    return (
        f"p=$(cat {d}/{run_id}.pid 2>/dev/null); "
        f"[ -n \"$p\" ] && [ -r \"/proc/$p/cmdline\" ] && tr '\\000' ' ' <\"/proc/$p/cmdline\" | grep -q {run_id}"
    )


def wrapper_command(run_id: str, command: str, *, base_dir: str | None = None, kind: JobKind = UPDATES) -> str:
    """Was auf dem Server entkoppelt läuft: pid merken, Befehl mit Ausgabe ins Protokoll, Rückgabecode atomar ablegen."""
    base_dir = kind.base_dir if base_dir is None else base_dir
    _check(run_id, base_dir, kind)
    f = shlex.quote(f"{base_dir}/{run_id}")
    # Die Subshell ist nötig, weil `upgrade_command` mit `exit $R` endet; sonst käme der Wrapper nie bis zur rc-Datei.
    return (
        f"echo $$ >{f}.pid.tmp && mv -f {f}.pid.tmp {f}.pid; "
        f"( {command} ) </dev/null >{f}.log 2>&1; "
        f"echo $? >{f}.rc.tmp; mv -f {f}.rc.tmp {f}.rc"
    )


def launch_command(run_id: str, command: str, *, base_dir: str | None = None, systemd_marker: str = SYSTEMD_MARKER,
                   kind: JobKind = UPDATES) -> str:
    """Startet `command` entkoppelt und kehrt nach höchstens ~5 s zurück (als root, siehe `as_root`).

    Ausgabe: `@@launch`, `@@method=...`, dann `@@pid=<n>` oder `@@nopid`; `@@exists`: der Lauf wurde schon gestartet."""
    base_dir = kind.base_dir if base_dir is None else base_dir
    _check(run_id, base_dir, kind)
    _check(run_id, systemd_marker, kind)
    w = shlex.quote(wrapper_command(run_id, command, base_dir=base_dir, kind=kind))
    # systemd ersetzt im Befehl von `systemd-run` `$NAME` und macht aus `$$` ein `$`. Ohne Verdoppeln stünde "$" statt
    # der Prozessnummer in der pid-Datei, und jeder Lauf galt nach ~10 s als abgebrochen.
    # `--expand-environment=no` kennt systemd 252 (Debian 12, Proxmox 8, Pi OS) noch nicht.
    ws = shlex.quote(wrapper_command(run_id, command, base_dir=base_dir, kind=kind).replace("$", "$$"))
    d = shlex.quote(base_dir)
    rid = run_id  # durch _check gesichert: nur Buchstaben, Ziffern, "_"
    unit = f"{kind.unit_prefix}{rid}"
    # stdin/stdout/stderr sind umgeleitet, sonst wartete der SSH-Aufruf auf den Hintergrundlauf. Mit systemd und mit
    # `setsid` führt der Wrapper eine eigene Sitzung (Sitzungsnummer = Nummer aus der pid-Datei); darüber beendet
    # `audit_job.stop_command` den ganzen Lauf.
    setsid = f"nohup setsid /bin/sh -c {w} </dev/null >/dev/null 2>&1 &"
    return (
        f"echo @@launch; d={d}; mkdir -p -m 700 \"$d\" || {{ echo @@nopid; exit 1; }}; "
        # Alte Protokolle aufräumen (nur eigene Dateien, älter als KEEP_DAYS Tage).
        f"find \"$d\" -maxdepth 1 -type f -name '{kind.prefix}_*' -mtime +{KEEP_DAYS} -exec rm -f {{}} + 2>/dev/null; "
        f"if [ -e \"$d/{rid}.pid\" ]; then echo @@exists; echo \"@@pid=$(cat \"$d/{rid}.pid\")\"; exit 0; fi; "
        f"if [ -d {shlex.quote(systemd_marker)} ] && command -v systemd-run >/dev/null 2>&1; then "
        f"if systemd-run --unit={unit} --collect --quiet --description='{kind.label} {rid}' "
        f"/bin/sh -c {ws} </dev/null 2>&1; then echo @@method=systemd; "
        f"else sleep 1; [ -s \"$d/{rid}.pid\" ] || {{ {setsid} echo @@method=setsid-fallback; }}; fi; "
        f"elif command -v setsid >/dev/null 2>&1; then {setsid} echo @@method=setsid; "
        f"else nohup /bin/sh -c {w} </dev/null >/dev/null 2>&1 & echo @@method=nohup; fi; "
        # Bis zu 5 s auf die pid-Datei warten (ohne "sleep 0.1" entsprechend länger).
        f"i=0; while [ ! -s \"$d/{rid}.pid\" ] && [ $i -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; i=$((i+1)); done; "
        f"if [ -s \"$d/{rid}.pid\" ]; then echo \"@@pid=$(cat \"$d/{rid}.pid\")\"; else echo @@nopid; fi"
    )


def poll_command(run_id: str, *, base_dir: str = JOB_DIR) -> str:
    """Stand eines Laufs (als root): `@@running` / `@@rc=<n>` / `@@lost` / `@@unknown`, dann `@@tail` mit dem Protokoll-Ende.
    Bei `@@rc` stehen davor unter `@@summary` apt-Zusammenfassung und Fehlerzeilen, die im Ende langer Läufe fehlen."""
    _check(run_id, base_dir)
    rid = run_id
    d = shlex.quote(base_dir)
    # Erst „läuft der Prozess noch?“, dann „gibt es die rc-Datei?“: andersherum gälte ein Lauf, der dazwischen fertig
    # wird, fälschlich als verloren (die rc-Datei entsteht vor dem Ende des Prozesses).
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
    progress: str | None = None  # letzte Protokollzeile, solange der Lauf läuft


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
        # Leere oder fremde Antwort (Kanal mitten im Aufruf verloren): sagt nichts über den Lauf aus,
        # nur der Server selbst antwortet mit "@@unknown".
        return Poll(state="noreply")
    return Poll(state="unknown")
