"""Viren-Wächter in Echtzeit (clamd mit clamonacc): Eignung eines Servers prüfen, Wächter ein- und ausschalten."""

from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

from .antivirus import (
    HELPER,
    HELPER_REALTIME_LOG_VERSION,
    REALTIME_NO_PACKAGE,
    REALTIME_NO_SYSTEMD,
    REALTIME_NOT_STARTED,
    helper_matches,
)

# clamd hält die Signaturen dauerhaft im Speicher (rund 1 GB), dazu kommen System und Dienste des Servers. Ein Server mit
# 2 GB zeigt in MemTotal etwas weniger (der Kernel behält einen Teil), darum liegt die Grenze unter 2048.
MIN_TOTAL_MB = 1800
MIN_AVAILABLE_MB = 1229

# Eigene Dateien unter /etc: /etc/clamav/clamd.conf baut das Paket bei Updates neu, die fassen wir nie an.
CONF_NAME = "clamav/nodvard-onaccess.conf"
WATCH_NAME = "clamav/nodvard-onaccess.watch"
EXCLUDE_NAME = "clamav/nodvard-onaccess.exclude"
STATE_NAME = "clamav/nodvard-onaccess.state"
DROPIN_NAME = "systemd/system/clamav-clamonacc.service.d/nodvard.conf"
CLAMD_CONF_NAME = "clamav/clamd.conf"
LOG_PATH = "/var/log/clamav/nodvard-onaccess.log"
STATE_KIND = "realtime"
"""Art der Merkzeile je Server (`patching.save_baseline`): ob der Wächter dort in Echtzeit läuft."""
DOWN_KIND = "realtime_down"
"""Art der Merkzeile je Server: seit wann der Wächter-Lauf auf den Minuten-Takt zurückgeschaltet hat (`since`), zu welchem
Einschalten (`changed_at` aus `STATE_KIND`). Eigene Zeile, damit der Lauf den Schalter nie überschreibt."""
POSITION_KIND = "realtime_log"
"""Art der Merkzeile je Server: bis zu welcher Zeile das Protokoll des Wächters gelesen ist."""
LOG_BATCH = 2000
"""So viele Zeilen des Protokolls holt ein Lauf höchstens; den Rest holt der nächste."""
CONFIRM_BATCH = 50
"""So viele Dateien prüft ein Aufruf der Gegenprüfung höchstens."""
MAX_LINE = 10**12 - 1

ON_MARK = "@@realtime-on"
OFF_MARK = "@@realtime-off"
MAX_FILESIZE_MB = 1000
_MAX_PATH_LEN = 4096
_HELPER_Q = shlex.quote(HELPER)
_HELPER_MATCHES = helper_matches() or "false"
SETTLE_S = 10
"""So lange muss clamonacc nach dem Start mit derselben PID weiterlaufen, damit das Einschalten als gelungen gilt."""

READINESS_COMMAND = (
    # Die Abfrage läuft ohne root; die sbin-Ordner stehen für normale Benutzer nicht im Suchpfad (wie bei STATUS_COMMAND).
    "PATH=\"$PATH:/usr/local/sbin:/usr/sbin:/sbin\"; "
    # Ohne systemd-detect-virt verraten sich Docker und Podman an ihrer Merkdatei.
    "echo @@virt; if command -v systemd-detect-virt >/dev/null 2>&1; then systemd-detect-virt -c 2>/dev/null; "
    "elif [ -f /.dockerenv ]; then echo docker; elif [ -f /run/.containerenv ]; then echo podman; fi; true; "
    "echo @@mem; (grep -E '^(MemTotal|MemAvailable):' /proc/meminfo 2>/dev/null; true); "
    # `CONFIG_FANOTIFY` steht je nach Kernel in /proc/config.gz oder in /boot; beides ist nicht überall lesbar.
    "echo @@fanotify; ([ -d /proc/sys/fs/fanotify ] && echo yes; "
    "(zcat /proc/config.gz 2>/dev/null || cat \"/boot/config-$(uname -r)\" 2>/dev/null) "
    "| grep -E '^(# )?CONFIG_FANOTIFY[ =]' 2>/dev/null; true); "
    "echo @@clamd; (command -v clamd 2>/dev/null || echo none); "
    "echo @@clamonacc; (command -v clamonacc 2>/dev/null || echo none); "
    # Kein `|| echo`: Bei einem gestoppten Dienst gibt systemctl "inactive" mit rc 3 aus.
    "echo @@clamd_state; (systemctl is-active clamav-daemon 2>/dev/null; true); "
    "echo @@clamonacc_state; (systemctl is-active clamav-clamonacc 2>/dev/null; true); "
    # Das Hilfsskript zuerst: Mit der engen sudo-Regel wäre `sudo -n true` ein abgelehnter Aufruf im Protokoll. Es zählt
    # nur, wenn es genau das mitgelieferte ist (`helper_matches`, ohne es auszuführen).
    "echo @@root; if [ \"$(id -u)\" = 0 ]; then echo root; "
    f"elif {_HELPER_MATCHES} && [ \"$(sudo -n {_HELPER_Q} version 2>/dev/null)\" -ge {HELPER_REALTIME_LOG_VERSION} ] "
    "2>/dev/null; then echo helper; "
    "elif sudo -n true 2>/dev/null; then echo sudo; else echo none; fi; "
    "echo @@apt; if command -v apt-get >/dev/null 2>&1; then echo yes; else echo no; fi; "
    "echo @@end"
)

_MEM_LINE = re.compile(r"^(MemTotal|MemAvailable):\s+(\d+)\s*kB", re.MULTILINE)
_FANOTIFY_CONFIG = re.compile(r"^(?:# )?CONFIG_FANOTIFY(?:=(\S+)| is not set)\s*$", re.MULTILINE)


@dataclass
class RealtimeReadiness:
    possible: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    container: str | None = None
    mem_total_mb: int | None = None
    mem_available_mb: int | None = None
    fanotify: bool | None = None
    clamd_installed: bool = False
    clamonacc_installed: bool = False
    clamd_active: bool | None = None
    clamonacc_active: bool | None = None
    """Läuft clamonacc gerade (`False` auch beim Neustart in Schleife, „activating“)? `None`: unbekannt."""
    root: str | None = None
    apt: bool | None = None


def _gb(mb: int) -> str:
    return f"{mb / 1024:.1f} GB".replace(".", ",")


def _sections(output: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    for line in output.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line.strip() + "\n"
    return sections


def _first_line(text: str) -> str:
    return next((ln for ln in text.splitlines() if ln), "")


def _found(text: str) -> bool:
    path = _first_line(text)
    return bool(path) and path != "none"


def _fanotify(text: str) -> bool | None:
    """`yes` oder `CONFIG_FANOTIFY=y` heißt ja; nur ein ausdrückliches Nein in der Kernel-Konfiguration heißt nein."""
    if "yes" in text.splitlines():
        return True
    values = [m.group(1) for m in _FANOTIFY_CONFIG.finditer(text)]
    if not values:
        return None
    return "y" in values


def _memory_mb(text: str) -> dict[str, int]:
    return {name: int(kb) // 1024 for name, kb in _MEM_LINE.findall(text)}


def parse_readiness(output: str) -> RealtimeReadiness:
    sections = _sections(output)
    virt = _first_line(sections.get("virt", ""))
    mem = _memory_mb(sections.get("mem", ""))
    state = _first_line(sections.get("clamd_state", ""))
    onacc = _first_line(sections.get("clamonacc_state", ""))
    root = _first_line(sections.get("root", ""))
    apt = _first_line(sections.get("apt", ""))
    r = RealtimeReadiness(
        possible=False,
        container=None if virt in ("", "none") else virt,
        mem_total_mb=mem.get("MemTotal"),
        mem_available_mb=mem.get("MemAvailable"),
        fanotify=_fanotify(sections.get("fanotify", "")),
        clamd_installed=_found(sections.get("clamd", "")),
        clamonacc_installed=_found(sections.get("clamonacc", "")),
        clamd_active=True if state == "active" else False if state in ("inactive", "failed") else None,
        clamonacc_active=True if onacc == "active" else False if onacc in ("inactive", "failed", "activating") else None,
        root=root if root in ("root", "helper", "sudo", "none") else None,
        apt=True if apt == "yes" else False if apt == "no" else None,
    )
    if "end" not in sections:
        r.reasons.append("Die Abfrage kam nicht vollständig zurück.")
        return r

    if r.container:
        r.reasons.append(f"Läuft in einem Container ({r.container}): Echtzeit braucht fanotify, das geht dort nicht.")
    if r.fanotify is False:
        r.reasons.append("Der Kernel des Servers bietet kein fanotify.")
    if r.mem_total_mb is None or r.mem_available_mb is None:
        # Lieber nein sagen, als einen Server einzurichten, dessen Speicher niemand kennt.
        r.reasons.append("Der Arbeitsspeicher des Servers ließ sich nicht lesen.")
    else:
        if r.mem_total_mb < MIN_TOTAL_MB:
            r.reasons.append(f"Zu wenig Arbeitsspeicher: {_gb(r.mem_total_mb)} insgesamt, nötig sind mindestens 2 GB.")
        # Läuft clamd schon, ist sein Speicher bereits belegt und gehört nicht mehr zum freien.
        if r.clamd_active is not True and r.mem_available_mb < MIN_AVAILABLE_MB:
            r.reasons.append(f"Zu wenig freier Arbeitsspeicher: {_gb(r.mem_available_mb)} frei, clamd braucht etwa 1,2 GB.")
    if r.root == "none":
        r.reasons.append(
            "Keine Rechte als root: Auf dem Server fehlt das aktuelle Hilfsskript von Nodvard Shield "
            "(Einrichtung erneut ausführen) oder sudo ohne Passwort."
        )
    if not r.clamd_installed:
        r.notes.append("Das Paket clamav-daemon fehlt noch; Nodvard Deck installiert es nicht selbst.")
    elif not r.clamonacc_installed:
        r.notes.append("clamonacc fehlt, er gehört zum Paket clamav-daemon.")
    r.possible = not r.reasons
    return r


def readiness_dict(r: RealtimeReadiness) -> dict[str, Any]:
    return asdict(r)


def clean_paths(paths: list[str]) -> list[str]:
    """Prüft Ordner für clamonacc (absolut, ohne Zeilenumbruch und `..`) und entfernt Doppelte und Schlussstriche."""
    cleaned = []
    for path in paths:
        valid = (
            isinstance(path, str) and path.startswith("/") and len(path) <= _MAX_PATH_LEN
            and not any(ch in path for ch in "\n\r\x00") and ".." not in path.split("/")
        )
        if not valid:
            raise ValueError(f"Ungültiger Ordner: {str(path)[:80]}")
        cleaned.append(path.rstrip("/") or "/")
    return list(dict.fromkeys(cleaned))


def _checked(watch: list[str], exclude: list[str], max_filesize_mb: int) -> tuple[list[str], list[str]]:
    watch, exclude = clean_paths(watch), clean_paths(exclude)
    if not watch:
        raise ValueError("Keine Ordner für den Wächter eingestellt.")
    size_ok = isinstance(max_filesize_mb, int) and not isinstance(max_filesize_mb, bool)
    if not size_ok or not 1 <= max_filesize_mb <= MAX_FILESIZE_MB:
        raise ValueError("Ungültige Dateigröße.")
    return watch, exclude


def _files(etc: str) -> list[str]:
    """Shell-Zeilen, die die Dateinamen unter `etc` in Variablen legen."""
    base = etc.rstrip("/")
    names = {"conf": CONF_NAME, "watch_file": WATCH_NAME, "exclude_file": EXCLUDE_NAME, "state": STATE_NAME,
             "dropin": DROPIN_NAME, "clamd_conf": CLAMD_CONF_NAME}
    return [f"{var}={shlex.quote(f'{base}/{name}')}" for var, name in names.items()]


def _write(variable: str, *args: str) -> str:
    """Shell-Zeile, die die schon gequoteten `args` Zeile für Zeile in die Datei der Variable schreibt."""
    return f"printf '%s\\n' {' '.join(args)} > \"${variable}\""


def enable_command(
    watch: list[str], exclude: list[str], *, max_filesize_mb: int, etc: str = "/etc",
    systemd_dir: str = "/run/systemd/system", wait_s: int = 120, settle_s: int = SETTLE_S, log_path: str = LOG_PATH,
) -> str:
    """Shell-Befehl (als root), der clamonacc für die Ordner einrichtet und startet; installiert nichts. Gelungen ist es
    erst, wenn clamonacc nach `settle_s` Sekunden noch mit derselben PID läuft. Sonst räumt er wie `disable_command` auf.

    Dasselbe macht `shield-root realtime-on`; `etc`, `systemd_dir`, `wait_s`, `settle_s` und `log_path` ändern nur die
    Tests."""
    watch, exclude = _checked(watch, exclude, max_filesize_mb)
    q = shlex.quote
    options = ["-F", "--fdpass", "--config-file=$conf", "--watch-list=$watch_file"]
    if exclude:
        options.append("--exclude-list=$exclude_file")
    options.append(f"--log={log_path}")
    exec_start = " ".join(["ExecStart=$onacc", *options])
    return "\n".join([
        "set -e",
        'PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"',
        *_files(etc),
        (
            "if ! command -v clamd >/dev/null 2>&1 || ! command -v clamonacc >/dev/null 2>&1; then "
            f"echo {q(REALTIME_NO_PACKAGE)}; exit 3; fi"
        ),
        f"if [ ! -d {q(systemd_dir)} ]; then echo {q(REALTIME_NO_SYSTEMD)}; exit 3; fi",
        "onacc=$(command -v clamonacc)",
        # Den Socket gibt die Konfiguration des Pakets vor; sie wird nur gelesen.
        """sock=$(awk '$1 == "LocalSocket" { print $2; exit }' "$clamd_conf" 2>/dev/null) || true""",
        '[ -n "$sock" ] || sock=/run/clamav/clamd.ctl',
        "umask 022",
        'mkdir -p "${conf%/*}" "${dropin%/*}"',
        # Nur beim ersten Einschalten: Sonst hielte der Rückweg den Stand nach dem ersten Mal für den alten.
        'if [ ! -e "$state" ]; then',
        "  {",
        "    if systemctl is-active --quiet clamav-daemon; then echo clamd_active=yes; else echo clamd_active=no; fi",
        "    if systemctl is-enabled --quiet clamav-daemon; then echo clamd_enabled=yes; else echo clamd_enabled=no; fi",
        '  } > "$state"',
        "fi",
        # Funde sperren nichts (`OnAccessPrevention no`) und verschieben nichts; sie landen nur im eigenen Protokoll.
        # Neue und verschobene Dateien prüft clamonacc gleich (`OnAccessExtraScanning`), nicht erst beim Öffnen. Das
        # Protokoll ohne Größengrenze: Sonst hörte clamonacc nach 1 MB auf zu schreiben, und Funde gingen verloren.
        _write("conf", '"LocalSocket $sock"', "'OnAccessExcludeUname clamav'", q(f"OnAccessMaxFileSize {max_filesize_mb}M"),
               "'OnAccessPrevention no'", "'OnAccessExtraScanning yes'", "'LogFileMaxSize 0'"),
        _write("watch_file", *map(q, watch)),
        _write("exclude_file", *map(q, exclude)) if exclude else 'rm -f "$exclude_file"',
        # `--fdpass`: clamd läuft als Benutzer clamav und kann Dateien von root sonst nicht lesen.
        _write("dropin", "'[Service]'", "'ExecStart='", f'"{exec_start}"'),
        "systemctl daemon-reload",
        "systemctl enable --now clamav-daemon",
        "systemctl enable clamav-clamonacc",
        # Nach dem Journal wie `disable_command` zurück: kein halb eingerichteter Dienst, der sich immer neu startet.
        "undo() {", *_undo_lines(), "}",
        (
            f"fail() {{ echo {q(REALTIME_NOT_STARTED)}; "
            "journalctl -u clamav-clamonacc -n 20 --no-pager 2>&1 || true; undo; exit 4; }"
        ),
        "systemctl restart clamav-clamonacc || fail",
        "n=0",
        (
            f'while [ "$n" -lt {int(wait_s)} ] && ! systemctl is-active --quiet clamav-clamonacc; do '
            "sleep 2; n=$((n + 2)); done"
        ),
        "systemctl is-active --quiet clamav-clamonacc || fail",
        # „active“ heißt nur: gestartet. clamonacc beendet sich oft erst Sekunden später (etwa wenn clamd noch lädt),
        # dann stünde der Wächter in Deck auf „an“, obwohl er nicht läuft.
        "p=$(systemctl show -p MainPID --value clamav-clamonacc) || p=0",
        f"sleep {int(settle_s)}",
        '[ "${p:-0}" != 0 ] && [ "$(systemctl show -p MainPID --value clamav-clamonacc)" = "$p" ] || fail',
        f"echo {ON_MARK}",
    ])


def disable_command(*, etc: str = "/etc") -> str:
    """Shell-Befehl (als root), der unsere Dateien entfernt und clamd in den Stand von vorher zurückversetzt.

    Dasselbe macht `shield-root realtime-off`."""
    return "\n".join(['PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"', *_files(etc), *_undo_lines(), f"echo {OFF_MARK}"])


def _undo_lines() -> list[str]:
    """Unsere Dateien weg, clamonacc aus, clamd wie vor dem ersten Einschalten; jede Zeile scheitert nie (`set -e`)."""
    return [
        "systemctl disable --now clamav-clamonacc || true",
        'rm -f "$dropin" "$conf" "$watch_file" "$exclude_file"',
        'rmdir "${dropin%/*}" 2>/dev/null || true',
        "systemctl daemon-reload || true",
        # Ohne State-Datei ist unbekannt, wie clamd vorher war: dann bleibt er, wie er ist.
        'if grep -qx clamd_active=no "$state" 2>/dev/null; then systemctl stop clamav-daemon || true; fi',
        'if grep -qx clamd_enabled=no "$state" 2>/dev/null; then systemctl disable clamav-daemon || true; fi',
        'rm -f "$state"',
    ]


def helper_args(enable: bool, watch: list[str], exclude: list[str], *, max_filesize_mb: int) -> list[str]:
    """Derselbe Auftrag wie `enable_command`/`disable_command` als Aufruf von `shield-root` (ab Version 11)."""
    if not enable:
        return ["realtime-off"]
    watch, exclude = _checked(watch, exclude, max_filesize_mb)
    return ["realtime-on", str(max_filesize_mb), *exclude, "--", *watch]


def switched(output: str, enable: bool) -> bool:
    """Steht die Marke des Einschaltens (`enable`) bzw. Ausschaltens als eigene Zeile in der Ausgabe?"""
    mark = ON_MARK if enable else OFF_MARK
    return any(line.strip() == mark for line in output.splitlines())


def log_command(line: int, *, log_path: str = LOG_PATH, batch: int = LOG_BATCH) -> str:
    """Shell-Befehl (als root): Läuft clamonacc, und die Zeilen des Protokolls nach Zeile `line` (höchstens `batch`).

    Dasselbe macht `shield-root realtime-log`; `log_path` und `batch` ändern nur die Tests."""
    if isinstance(line, bool) or not isinstance(line, int) or not 0 <= line <= MAX_LINE:
        raise ValueError("Ungültige Zeile im Protokoll.")
    return "\n".join([
        f"f={shlex.quote(log_path)}",
        "if systemctl is-active --quiet clamav-clamonacc; then echo @@active=yes; else echo @@active=no; fi",
        "n=0",
        # Nur ganze Zeilen: Eine, die clamonacc gerade schreibt, kommt beim nächsten Lauf.
        '[ ! -r "$f" ] || n=$(wc -l < "$f")',
        "n=$((n + 0))",
        f"l={line}",
        # Weniger Zeilen als gemerkt: Das Protokoll ist neu, also von vorn.
        '[ "$l" -le "$n" ] || l=0',
        f"e=$((l + {int(batch)}))",
        '[ "$e" -le "$n" ] || e=$n',
        'echo "@@lines=$l-$e"',
        'if [ "$e" -gt "$l" ]; then sed -n "$((l + 1)),${e}p" "$f"; fi',
        "echo @@end",
    ])


@dataclass
class LogChunk:
    active: bool
    start: int
    end: int
    lines: list[str]


_LINES_RE = re.compile(r"^@@lines=(\d{1,12})-(\d{1,12})$")


def parse_log(output: str) -> LogChunk | None:
    """Liest die Antwort von `log_command`; `None`, wenn sie nicht vollständig ist.

    Kopf sind die ersten beiden Zeilen, Ende die letzte `@@end`: Eine Zeile im Protokoll (ein Dateiname) kann beides
    vortäuschen, aber nicht vor dem Kopf oder nach dem letzten Ende stehen."""
    lines = output.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if len(lines) < 3 or lines[-1] != "@@end" or lines[0] not in ("@@active=yes", "@@active=no"):
        return None
    match = _LINES_RE.match(lines[1])
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    body = lines[2:-1]
    if end < start or len(body) != end - start:
        return None
    return LogChunk(active=lines[0] == "@@active=yes", start=start, end=end, lines=body)


# Zeile von clamonacc zu einem Fund: `<pfad>: <signatur> FOUND`, mit `LogTime` davor `<datum> -> `. Signaturnamen haben
# keine Leerzeichen, der Pfad schon.
_FOUND_LINE_RE = re.compile(
    r"^(?:[A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \d{4} -> )?(?P<path>/.*): (?P<sig>\S+) FOUND$"
)


def found_lines(lines: list[str]) -> list[tuple[str, str]]:
    """Die Funde im Protokoll als (Pfad, Signatur), jeder Pfad einmal; Pfade mit `..` zählen nicht."""
    found: dict[str, str] = {}
    for line in lines:
        match = _FOUND_LINE_RE.match(line)
        if match and ".." not in match.group("path").split("/"):
            found.setdefault(match.group("path"), match.group("sig"))
    return list(found.items())


def unreadable_found(lines: list[str]) -> int:
    """Zeilen, die auf `FOUND` enden, aber nicht wie eine Fund-Zeile aussehen: Sie sollen auffallen, nicht verloren gehen."""
    return sum(1 for line in lines if line.rstrip().endswith(" FOUND") and not _FOUND_LINE_RE.match(line))


def confirm_command(paths: list[str]) -> str:
    """Shell-Befehl (als root): prüft genau diese Dateien noch einmal über clamd. Dasselbe macht `shield-root
    realtime-confirm`."""
    paths = clean_paths(paths)
    if not 1 <= len(paths) <= CONFIRM_BATCH:
        raise ValueError("Ungültige Zahl von Dateien für die Gegenprüfung.")
    return f"clamdscan --fdpass --no-summary {' '.join(shlex.quote(p) for p in paths)}\necho \"@@rc=$?\""


_CONFIRM_RC_RE = re.compile(r"^@@rc=(\d{1,3})$")


def parse_confirm(output: str, paths: list[str]) -> dict[str, str] | None:
    """Bestätigte Funde je Pfad aus `paths`; `None`, wenn clamd nicht prüfen konnte (Rückgabewert über 1)."""
    lines = output.rstrip("\n").split("\n")
    match = _CONFIRM_RC_RE.match(lines[-1]) if lines else None
    if match is None or int(match.group(1)) > 1:
        return None
    wanted = set(paths)
    confirmed: dict[str, str] = {}
    for line in lines[:-1]:
        found = _FOUND_LINE_RE.match(line)
        if found and found.group("path") in wanted:
            confirmed.setdefault(found.group("path"), found.group("sig"))
    return confirmed


def outside_watch(path: str, watch: list[str], exclude: list[str]) -> bool:
    """Liegt der Pfad außerhalb der Ordner des Wächters oder in einer Ausnahme? Dann verschiebt Deck nichts selbst."""
    def inside(folder: str) -> bool:
        base = folder.rstrip("/")
        return base == "" or path == base or path.startswith(base + "/")

    return not any(inside(f) for f in watch) or any(inside(f) for f in exclude)
