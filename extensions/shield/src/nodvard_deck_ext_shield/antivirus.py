"""ClamAV-Scans, Quarantäne und Lynis auf den Servern: baut die Befehle und wertet die Ausgaben aus.

Ausführen, Speichern und Melden steht in `defender.py`.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import secrets
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nodvard_sdk import root_helper_path

# Die Quarantäne liegt auf dem betroffenen Server selbst.
QUARANTINE_DIR = "/var/lib/nexus-quarantine"

# Die Ordner hier sind Ziele der Scans auf den Servern, keine eigenen Zwischendateien (`nosec B108` für Bandit).
QUICK_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root"]  # nosec B108
DEEP_PATHS = ["/"]
# Der Echtzeit-Wächter scannt in kurzen Abständen die zuletzt geänderten Dateien dieser Ordner; inotify bräuchte auf
# jedem Server einen Agenten.
WATCH_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root", "/opt"]  # nosec B108
# Nur für den Wächter (Schnell- und Tiefenscan prüfen /dev/shm weiter): IPC-Dateien von pmxcfs/corosync (Proxmox) ändern
# sich ständig, und clamscan lädt bei jedem Lauf die ganze Datenbank.
WATCH_EXCLUDED = ["/dev/shm/qb-*"]  # nosec B108

# clamdscan meldet eine zwischen `find` und Scan verschwundene Datei mit Code 2 und "ERROR: Can't access file ..." (1.0.5);
# die übrigen sind trotzdem geprüft. Erweitertes grep-Muster für die erlaubten Zeilen: genau diese Meldung und Funde.
_CLAMD_SKIP_OK = "^(ERROR|WARNING): Can.t access file | FOUND$"

# Höchstens so viele neue Dateien prüft ein Wächterlauf (Laufzeit); den Rest meldet die Shell als `Skipped files: N`.
WATCH_MAX_FILES = 2000
# `clamscan --file-list` liest je Zeile höchstens 1023 Zeichen (`fgets(buff, 1024, ...)`); ein längerer Pfad zerfiele in
# Stücke, die auf nicht vorhandene Dateien zeigen. Solche Pfade gehen als Argumente durch (`_ODD_NAME_TEST`).
_LONG_PATH_BYTES = 1023

# Tiefenscan: virtuelle und flüchtige Dateisysteme nie durchsuchen.
_ALWAYS_EXCLUDED = ["/proc", "/sys", "/dev", "/run", QUARANTINE_DIR]

# Rückgabecode-Marke eines Scans: je Lauf eine Zufallsmarke `@@scan-rc-<16 hex>=`. Eine feste ließe sich in einen Dateinamen
# schreiben (`/tmp/x@@<marke>=0`), die Auswertung schnitte dort ab und der Fund verschwände. Befehlsbau und Auswertung
# brauchen dieselbe Marke (`build_*_command(..., mark=m)`, `parse_scan_output(..., m)`).
_RC_MARK_RE = re.compile(r"@@scan-rc-[0-9a-f]{16}=")
NO_ROOT = "@@noroot"
NO_ROOT_HELPER = "@@noroot-helper"
"""Zusätzlich zu NO_ROOT: Auf dem Server liegt ein Hilfsskript, das nicht das mitgelieferte ist (`no_root_message`)."""
NO_ROOT_HELPER_UNREADABLE = "@@noroot-helper-unreadable"
"""Statt NO_ROOT_HELPER: Das Hilfsskript liegt da, der angemeldete Benutzer kann es aber nicht lesen, also weder seine
Prüfsumme noch seine Version prüfen."""
NO_ROOT_MESSAGE = (
    "Keine root-Rechte: Das Dashboard ist auf diesem Server nicht als root angemeldet und "
    "darf sudo nicht ohne Passwort nutzen."
)


def as_root(command: str, *, required: bool = True) -> str:
    """Führt `command` als root aus: direkt, wenn schon root, sonst über `sudo -n` (ohne Passwortabfrage).

    Geht beides nicht: bei `required` Abbruch mit NO_ROOT, sonst als normaler Benutzer (Scan nur lesbarer Dateien)."""
    inner = shlex.quote(command)
    fallback = f"sh -c {inner}" if not required else f"echo '{NO_ROOT}'; exit 126"
    return (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {inner}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {inner}; "
        f"else {fallback}; fi"
    )


HELPER = root_helper_path("shield")
"""Das Hilfsskript dieses Moduls auf dem Server (`root-helper/shield-root.sh`, eingerichtet vom Einrichtungsbefehl)."""
HELPER_FILE = Path(__file__).resolve().parents[2] / "root-helper" / "shield-root.sh"
"""Das mitgelieferte Skript, genau die Datei, die der Einrichtungsbefehl auf die Server legt."""


def _bundled_helper(path: Path) -> tuple[int, str] | None:
    """Version (`HELPER_VERSION=<n>`) und sha256 des mitgelieferten Skripts, `None` ohne lesbare Datei oder Version."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    match = re.search(rb"^HELPER_VERSION=([0-9]{1,4})$", raw, re.MULTILINE)
    return (int(match.group(1)), hashlib.sha256(raw).hexdigest()) if match else None


HELPER_BUNDLED = _bundled_helper(HELPER_FILE)
HELPER_READ_VERSION = 2
"""Ab dieser Version kennt das Hilfsskript `guard`, `detect-templates` und `refresh-packages`."""
HELPER_ACTION_VERSION = 3
"""Ab dieser Version kennt das Hilfsskript `quarantine`, `restore`, `delete`, `ban`/`unban` und `install`."""
HELPER_UPDATE_VERSION = 4
"""Ab dieser Version kennt das Hilfsskript `reboot`, `upgrade-start` und `upgrade-poll`."""
HELPER_AUDIT_VERSION = 5
"""Ab dieser Version kennt das Hilfsskript `audit-start`, `audit-poll` und `audit-stop`."""
HELPER_SCAN_VERSION = 6
"""Ab dieser Version kennt das Hilfsskript `scan` und `watch`."""
HELPER_QUARANTINE_VERSION = 8
"""Ab dieser Version verschiebt das Hilfsskript nur, was ClamAV selbst findet (zuerst am Ort, dann im Prüfordner), und
legt nur an den Ort zurück, den es dabei hinterlegt hat (`restore <name>`), auch bei zwei gleichzeitigen Aufrufen mit
demselben Namen."""
HELPER_REALTIME_VERSION = 13
"""Ab dieser Version kennt das Hilfsskript `realtime-on` und `realtime-off`, meldet „an“ erst, wenn clamonacc nach dem
Start weiterläuft, und räumt sonst wie `realtime-off` auf."""
HELPER_REALTIME_LOG_VERSION = 12
"""Ab dieser Version kennt das Hilfsskript `realtime-log` und `realtime-confirm`."""
HELPER_QUARANTINE_STATE_VERSION = 14
"""Ab dieser Version kennt das Hilfsskript `quarantine-state`."""

REALTIME_NO_PACKAGE = "Auf dem Server fehlt das Paket clamav-daemon."
REALTIME_NO_SYSTEMD = "Ohne systemd lässt sich Echtzeit nicht einrichten."
REALTIME_NOT_STARTED = "clamonacc startet nicht:"


def helper_matches() -> str | None:
    """Shell-Bedingung „das Hilfsskript liegt auf dem Server und ist Byte für Byte das mitgelieferte“ (sha256, gelesen
    als der angemeldete Benutzer, ohne das Skript auszuführen). Erst danach ruft das Modul es auf, auch für `version`:
    Ein verändertes oder veraltetes Skript läuft nie. `None`, wenn das mitgelieferte Skript fehlt."""
    if HELPER_BUNDLED is None:
        return None
    helper = shlex.quote(HELPER)
    return f"[ -x {helper} ] && [ \"$(sha256sum < {helper} 2>/dev/null)\" = \"{HELPER_BUNDLED[1]}  -\" ]"


def _no_root(required: bool, inner: str, matches: str) -> str:
    """Letzter Zweig ohne root: als normaler Benutzer oder NO_ROOT; liegt ein Hilfsskript da, das nicht das mitgelieferte
    ist, dazu `@@noroot-helper <Version aus seinem Text>` für `no_root_message`, bei einem unlesbaren
    `@@noroot-helper-unreadable`."""
    if not required:
        return f"sh -c {inner}"
    helper = shlex.quote(HELPER)
    return (
        f"echo '{NO_ROOT}'; if [ -e {helper} ] && ! {{ {matches}; }}; then if [ -r {helper} ]; then "
        f"echo \"{NO_ROOT_HELPER} $(sed -n 's/^HELPER_VERSION=//p' {helper} 2>/dev/null | head -n 1)\"; "
        f"else echo '{NO_ROOT_HELPER_UNREADABLE}'; fi; fi; exit 126"
    )


def via_helper(args: Sequence[str], command: str, *, version: int, required: bool = True) -> str:
    """Wie `as_root(command, required=...)`, aber ohne root-Anmeldung zuerst über das Hilfsskript: Ist es genau das
    mitgelieferte (`helper_matches`) und kann das mindestens `version`, läuft `sudo -n <skript> <args>`, das dasselbe
    ausgibt wie `command`. Sonst geht es auf dem alten Weg weiter (`sudo -n sh -c`, dann je nach `required` NO_ROOT oder
    als normaler Benutzer)."""
    matches = helper_matches()
    if matches is None or HELPER_BUNDLED is None or HELPER_BUNDLED[0] < version:
        return as_root(command, required=required)
    helper = shlex.quote(HELPER)
    inner = shlex.quote(command)
    call = " ".join(shlex.quote(a) for a in args)
    # Zuerst die Prüfsumme, die der angemeldete Benutzer selbst liest: Ohne passendes Skript kein sudo-Aufruf (jeder
    # landet im Protokoll, ohne sudo-Recht sogar als Mail an root). `version` prüft danach, ob sudo das Skript erlaubt;
    # ohne Recht ist die Ausgabe leer, der Vergleich scheitert, und es geht auf dem alten Weg weiter.
    return (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {inner}; "
        f"elif {matches} && [ \"$(sudo -n {helper} version 2>/dev/null)\" -ge {version} ] 2>/dev/null; then "
        f"sudo -n {helper} {call}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {inner}; "
        f"else {_no_root(required, inner, matches)}; fi"
    )


_NO_ROOT_HELPER_RE = re.compile(rf"^{NO_ROOT_HELPER}(?: (.*))?$", re.MULTILINE)


HELPER_RERUN = "Bitte den Server neu einrichten (Einrichtungsbefehl mit „Root-Rechte ohne Passwort“)."


def no_root_message(output: str | None) -> str:
    """Die Meldung zu NO_ROOT: Liegt ein Hilfsskript auf dem Server, das nicht das mitgelieferte ist oder sich nicht lesen
    lässt, sagt sie das und verlangt „neu einrichten“. Sonst `NO_ROOT_MESSAGE`. Höchstens 200 Zeichen: Anzeigen mit
    einer Zeile schneiden sonst den Teil mit „neu einrichten“ ab."""
    text = output or ""
    if HELPER_BUNDLED is not None and NO_ROOT_HELPER_UNREADABLE in text.splitlines():
        what = "lässt sich nicht lesen"
    else:
        match = _NO_ROOT_HELPER_RE.search(text)
        if match is None or HELPER_BUNDLED is None:
            return NO_ROOT_MESSAGE
        # Die Zahl kommt aus dem Text des Skripts auf dem Server: nur eine schlichte Zahl zählt.
        version, bundled = (match.group(1) or "").strip(), HELPER_BUNDLED[0]
        found = int(version) if re.fullmatch(r"[0-9]{1,4}", version) else None
        if found is not None and found < bundled:
            what = f"ist veraltet (Version {found}, Dashboard hat {bundled})"
        elif found == bundled:
            what = f"ist verändert (Version {bundled}, anderer Inhalt)"
        else:
            what = "ist verändert oder neuer als dieses Dashboard"
    return f"Das Hilfsskript auf dem Server {what}, darum fehlen die root-Rechte. {HELPER_RERUN}"


def new_rc_mark() -> str:
    """Frische Zufallsmarke für einen Scan-Lauf: `@@scan-rc-<16 hex>=`."""
    return f"@@scan-rc-{secrets.token_hex(8)}="


def _checked_mark(mark: str | None) -> str:
    """Die Marke kommt in doppelte Anführungszeichen der Shell: nur genau dieses Format."""
    if mark is None:
        return new_rc_mark()
    if not _RC_MARK_RE.fullmatch(mark):
        raise ValueError(f"Ungueltige Scan-Marke: {mark!r}")
    return mark


@dataclass
class ScanResult:
    status: str  # clean | infected | error
    files_scanned: int | None = None
    findings: list[tuple[str, str]] = field(default_factory=list)  # (Pfad, Signatur)
    errors: list[str] = field(default_factory=list)
    error: str | None = None
    # Anzahl Funde: mindestens die gelesenen Fund-Zeilen, sonst die Zahl der Zusammenfassung oder 1 (Code 1 ohne Fund-Zeile).
    infected: int = 0
    # Fund-Zeilen nicht verlässlich (Zeilenumbruch im Dateinamen, Zeilen, die ClamAV nie ausgibt, unstimmige Zahlen,
    # Marke fehlt): `findings` kann gefälschte Pfade enthalten, der Defender (als root) verschiebt dann nichts selbst.
    unreliable: bool = False
    # Davon der Teil, der die Pfade betrifft (Zeilenumbruch im Namen, Pfad außerhalb der Wurzeln ...). `unreliable` gilt
    # zusätzlich, wenn der Wächter Dateien nicht geprüft hat (`skipped`).
    paths_unsure: bool = False
    # So viele neue Dateien hat der Wächter wegen der Obergrenze `WATCH_MAX_FILES` nicht geprüft.
    skipped: int = 0
    # Die Sperre des Servers war belegt (`guard_command`): Der Lauf hat gar nicht angefangen, es lief schon ein anderer.
    busy: bool = False
    # `timeout` auf dem Server hat den Lauf beendet (`guard_command`).
    timed_out: bool = False
    # Eigener Hinweis für Lauf und Funde, wenn nichts automatisch verschoben wird (statt des allgemeinen zu `unreliable`).
    hint: str | None = None


def _q(value: str) -> str:
    return shlex.quote(value)


def _roots_label(mark: str) -> str:
    """Anfang der Zeilen mit den aufgelösten Wurzeln: `@@scan-roots-<hex>=` (Zufallszahl der Marke des Laufs)."""
    return "@@scan-roots-" + mark[len("@@scan-rc-"):]


def _run_label(mark: str, word: str) -> str:
    """Eine Zeile, die nur `guard_command` schreibt: `@@scan-<word>-<16 hex>` mit der Zufallszahl der Marke des Laufs.

    Ein Dateiname kann sie nicht vorwegnehmen (die Zahl ist erst beim Befehlsbau bekannt)."""
    return f"@@scan-{word}-{mark[len('@@scan-rc-'):-1]}"


def _resolve_roots_command(paths: list[str], mark: str) -> str:
    """Shell-Teil, der als Allererstes je Wurzel eine Zeile `<_roots_label><aufgelöster Pfad>` schreibt.

    `parse_scan_output` liest genau diese ersten Zeilen; ein Dateiname kann sie nicht vorwegnehmen."""
    # clamscan und clamdscan melden Funde mit aufgelöstem Pfad (ist `/home` ein Link auf `/data/home`, passte der Fund nie
    # zur Einstellung); darum löst die Shell jede Wurzel selbst auf (`cd -P`, sonst `readlink -f`, das bei fehlendem letztem
    # Stück den Pfad unverändert gibt). Pfade mit Zeilenumbruch/Wagenrücklauf und nicht auflösbare ergeben eine leere Zeile.
    label = _roots_label(mark)
    quoted = " ".join(_q(p) for p in paths)
    return (
        # `$(...)` verlöre einen Umbruch am Ende, darum NL mit angehängtem `x` bilden und das `x` wieder abschneiden.
        "NL=$(printf '\\nx'); NL=${NL%x}; CR=$(printf '\\r'); "
        f"for P in {quoted}; do "
        'D=$(cd -P -- "$P" 2>/dev/null && pwd -P || readlink -f -- "$P" 2>/dev/null); '
        'case "$D" in *"$NL"*|*"$CR"*) D=;; esac; '
        f"printf '%s\\n' \"{label}$D\"; done; "
    )


_ERE_SPECIAL = frozenset(".[]()*+?{}|^$\\")


# Nur ein angehängtes `/*` oder `/**` ("der Inhalt dieses Ordners") fällt weg, andere Platzhalter stehen wörtlich.
def _strip_trailing_wildcards(folder: str) -> str:
    """Schneidet wie `(/\\*+)+/*$` am Ende ab, aber in einem Durchgang, also auch bei sehr langen Eingaben schnell."""
    end = len(folder.rstrip("/"))
    stripped = False
    while end and folder[end - 1] == "*":
        start = end
        while start and folder[start - 1] == "*":
            start -= 1
        if not start or folder[start - 1] != "/":
            break
        end, stripped = start - 1, True
    return folder[:end] if stripped else folder


_MAX_EXCLUDE_BYTES = 511
"""ClamAV kürzt den Pfad in `match_regex` auf 512 Bytes; ein längerer Ausnahmepfad träfe nie, einer mit genau 512 Bytes auch
Nachbarn."""


def exclude_folder(folder: object) -> str | None:
    """Ein Eintrag aus „Ausgenommene Ordner“ (`exclude_paths`) als vereinfachter absoluter Pfad, oder `None`.

    Gilt für Scan und Wächter gleich: nur absolute Pfade ohne Zeilenumbruch, Wagenrücklauf und NUL."""
    if not isinstance(folder, str) or not folder.startswith("/") or any(ch in folder for ch in "\n\r\x00"):
        return None
    # `..` wird verworfen statt aufgelöst: `/home/../etc` nähme sonst `/etc` aus, mehr als gemeint.
    if ".." in folder.split("/"):
        return None
    # Vereinfacht wird wie bei `find` (Schlussstrich, `//`, `/./`).
    folder = _strip_trailing_wildcards(folder) or "/"
    folder = "/" + posixpath.normpath(folder).lstrip("/")
    # `/` selbst zählt nie, sonst liefe gar nichts.
    return None if folder == "/" else folder


def scan_exclude_regex(folder: object) -> str | None:
    """Ausdruck für `clamscan --exclude-dir`, der genau diesen Ordner samt Inhalt trifft, oder `None`.

    Welche Einträge zählen, regelt `exclude_folder`; zu lange Pfade (`_MAX_EXCLUDE_BYTES`) werden nicht ausgenommen."""
    folder = exclude_folder(folder)
    if folder is None or len(folder.encode("utf-8", "replace")) > _MAX_EXCLUDE_BYTES:
        return None
    # ClamAV liest den Wert als regulären Ausdruck (POSIX ERE) und sucht ihn im Pfad jedes Ordners; den Anker `^` muss
    # er selbst liefern. Darum Sonderzeichen maskieren (`/srv/a+b` träfe sonst anderes) und hinter dem Namen `/` oder
    # das Ende verlangen, sonst nähme `/srv/backup` auch `/srv/backups-wichtig` aus.
    escaped = "".join("\\" + ch if ch in _ERE_SPECIAL else ch for ch in folder)
    return f"^{escaped}(/|$)"


def build_scan_command(
    paths: list[str], *, max_filesize_mb: int = 50, exclude: list[str] | None = None, mark: str | None = None,
) -> str:
    """`clamscan` rekursiv, nur Funde ausgeben (`-i`), mit Zusammenfassung am Ende für die Dateizahl.

    `mark`: Zufallsmarke des Laufs (`new_rc_mark()`), dieselbe geht an `parse_scan_output`; sonst wird eine gezogen."""
    mark = _checked_mark(mark)
    excluded = [*_ALWAYS_EXCLUDED, *(exclude or [])]
    parts = ["clamscan", "-r", "-i", "--stdout", "--cross-fs=no" if paths == ["/"] else "",
             f"--max-filesize={max_filesize_mb}M", f"--max-scansize={max_filesize_mb * 4}M"]
    regexes = (scan_exclude_regex(p) for p in excluded)
    parts += [f"--exclude-dir={_q(regex)}" for regex in regexes if regex]
    parts += [_q(p) for p in paths]
    cmd = " ".join(p for p in parts if p)
    # Der Rückgabecode wird angehängt, weil 1 hier "Fund" heißt und nicht Fehler.
    return f"{_resolve_roots_command(paths, mark)}{cmd} 2>&1; echo \"{mark}$?\""


# Pfade mit Zeilenumbruch/Wagenrücklauf oder ab 1023 Bytes dürfen nicht in die `--file-list` (ein Pfad je Zeile,
# höchstens 1023 Zeichen): Sie zerfielen in Bruchstücke, die echte Datei bliebe ungeprüft. `find` gibt sie stapelweise
# als Argumente an einen eigenen `clamscan`; ein Fund darin gilt nie als eindeutig lesbar (`Unusual file names: N`).
_ODD_NAME_LABEL = "Unusual file names"
# Braucht GNU find: busybox kennt `-exec ... {} +` in `\\( ... \\)` und `-size -50M` nicht (dort prüft der Wächter nie).
_ODD_NAME_TEST = '\\( -path "*$NL*" -o -path "*$CR*" -o -path "$LONG" \\)'
# X ist die Ausgabe, Y je Stapel "Rückgabecode Dateizahl". Die Argumente beginnen immer mit dem absoluten Startpfad von
# `find`, nie mit `-`. Der Lauf ist selten und lädt die Datenbank neu, auch mit clamd: Richtigkeit geht vor Tempo.
_ODD_NAME_SCAN = (
    "sh -c 'o=$0; r=$1; shift; n=$#; clamscan -i --stdout --no-summary \"$@\" >> \"$o\" 2>&1; "
    "echo \"$? $n\" >> \"$r\"' \"$X\" \"$Y\" {} +"
)
# Rückgabecodes der Sonderläufe in den Gesamtcode (R): 127 (nicht installiert) vor 1 (Fund) vor allem anderen; ein
# Fehler (2, Absturz ...) nur, wenn bisher alles sauber war oder nur Code 2 vorlag.
_ODD_NAME_MERGE = (
    'T=0; while read -r r n; do T=$((T + ${n:-0})); case "$r" in 0) ;; '
    '1) [ "$R" -eq 127 ] || R=1;; '
    '*) [ "$R" -eq 0 ] || [ "$R" -eq 2 ] && R=$r;; esac; done < "$Y"; '
    f'[ "$T" -eq 0 ] || echo "{_ODD_NAME_LABEL}: $T"; '
)
# Obergrenze: `awk` schreibt die ersten `WATCH_MAX_FILES` Pfade in die Liste und zählt den Rest in die Datei K; es liest
# alles, damit `find` nie per SIGPIPE abbricht, bevor es Sondernamen übergibt (`head` wirft beim blockweisen Lesen
# Zeilen weg). `Skipped files: N` steht hinter allen Dateinamen, der letzte Treffer gilt.
_SKIPPED_LABEL = "Skipped files"
_CAP_LIST = (
    'L="$L" K="$K" awk \'NR <= ' + str(WATCH_MAX_FILES) + ' { print > ENVIRON["L"] } '
    'END { print (NR > ' + str(WATCH_MAX_FILES) + ' ? NR - ' + str(WATCH_MAX_FILES) + ' : 0) > ENVIRON["K"] }\''
)
_SKIPPED_REPORT = (
    'S=$(tr -cd 0-9 < "$K"); [ "${S:-0}" -eq 0 ] || ' + f'echo "{_SKIPPED_LABEL}: $S"; '
)


def _find_literal(path: str) -> str:
    """Ein Pfad als Muster für `find -path`, das nur genau ihn trifft: `*`, `?`, `[` und `\\` stehen wörtlich."""
    return "".join("\\" + ch if ch in "*?[]\\" else ch for ch in path)


def _prune_expression(folders: list[str]) -> str:
    """`find`-Ausdruck, der die ausgenommenen Ordner samt Inhalt gar nicht erst betritt (`-prune`), oder leer.

    Die Ordner kommen wie beim Scan aus „Ausgenommene Ordner“ (`exclude_paths`); `exclude_folder` prüft sie."""
    # `find` nennt Pfade mit einfachen Schrägstrichen (`/a//b` griffe sonst nie), darum die Vereinfachung in
    # `exclude_folder`. Ist eine Wurzel selbst ein Link, vergleicht `find` mit ihrem Namen, nicht mit dem Ziel.
    tests: list[str] = []
    for entry in folders:
        folder = exclude_folder(entry)
        if folder is None:
            continue
        literal = _find_literal(folder)
        tests += [f"-path {_q(literal)}", f"-path {_q(literal.rstrip('/') + '/*')}"]
    return f"\\( {' -o '.join(tests)} \\) -prune -o " if tests else ""


def build_watch_command(
    paths: list[str], *, minutes: int, max_filesize_mb: int = 50, exclude: list[str] | None = None,
    use_clamd: bool = False, mark: str | None = None, skip_folders: list[str] | None = None,
) -> str:
    """Der kurze Wächter-Lauf: prüft nur Dateien, die in den letzten `minutes` Minuten neu oder geändert wurden.

    `exclude`: find-Muster (Standard `WATCH_EXCLUDED`), `skip_folders`: ausgenommene Ordner, `use_clamd`: clamd."""
    mark = _checked_mark(mark)
    finds = " ".join(_q(p) for p in paths)
    skip = " ".join(f"-not -path {_q(p)}" for p in (WATCH_EXCLUDED if exclude is None else exclude))
    clamscan = 'clamscan -i --stdout --file-list="$L" 2>&1; R=$?'
    if use_clamd:
        # Antwortet clamd (`--ping 1`), prüft der Wächter über ihn (`--fdpass`), sonst mit clamscan. Nur ein sauberer
        # Lauf zählt: Code 0, oder 1/2 mit nur `_CLAMD_SKIP_OK` (verschwundene Dateien sind Alltag). Alles andere wird
        # mit clamscan wiederholt: Code 1 kommt auch mit späteren Verbindungsfehlern, die Dateien danach blieben ungeprüft.
        scan = (
            'C=; if clamdscan --ping 1 >/dev/null 2>&1; then '
            'O=$(clamdscan --fdpass --no-summary -i --file-list="$L" 2>&1); R=$?; '
            'if [ "$R" -eq 0 ] || { [ "$R" -le 2 ] && '
            "! printf '%s\\n' \"$O\" | grep -qvE '" + _CLAMD_SKIP_OK + "'; }; then "
            'C=1; [ -z "$O" ] || printf \'%s\\n\' "$O"; '
            'echo "Scanned files: $(wc -l < "$L" | tr -d \' \')"; fi; fi; '
            f'if [ -z "$C" ]; then {clamscan}; fi'
        )
    else:
        scan = clamscan
    return (
        # Die Dateiliste darf nicht selbst ein Treffer sein: `mktemp` legt sie ohne TMPDIR (über SSH/sudo nie gesetzt)
        # im überwachten /tmp an, clamscan liefe bei jedem Lauf. Darum /run (nur root, nie überwacht), und `find`
        # schließt die vier Dateien (L, X, Y, K) zusätzlich aus.
        _resolve_roots_command(paths, mark)
        # Beendet das Zeitlimit den Lauf (`guard_command`, erst TERM), bleiben die vier Dateien nicht liegen.
        + "trap 'rm -f \"$L\" \"$X\" \"$Y\" \"$K\" 2>/dev/null; exit 143' TERM; "
        "L=$(mktemp -p /run 2>/dev/null || mktemp); X=$(mktemp -p /run 2>/dev/null || mktemp); "
        "Y=$(mktemp -p /run 2>/dev/null || mktemp); K=$(mktemp -p /run 2>/dev/null || mktemp); "
        # LONG: 1023 `?` und ein `*`; `find` läuft mit `LC_ALL=C`, damit ein `?` ein Byte trifft und kein Zeichen.
        f"LONG=$(printf '%0{_LONG_PATH_BYTES}d' 0 | tr 0 '?'); LONG=\"$LONG*\"; "
        # `-H`: Ein Ordner, der selbst eine Verknüpfung ist (`/home` -> `/data/home`), wird durchsucht, nicht übergangen.
        f"LC_ALL=C find -H {finds} -xdev {_prune_expression(skip_folders or [])}"
        # Auch `-cmin`: Wer per Freigabe (Samba, Nextcloud-Sync, `cp -p`, `rsync -a`) kopiert, behält die alte Änderungszeit
        # (mtime); die Zeit des Eintrags (ctime) setzt das System beim Anlegen und lässt sich nicht vorgeben.
        f"-type f \\( -mmin -{int(minutes)} -o -cmin -{int(minutes)} \\) -size -{int(max_filesize_mb)}M "
        f"-not -path {_q(QUARANTINE_DIR + '/*')} {skip} -not -path \"$L\" -not -path \"$X\" -not -path \"$Y\" "
        f"-not -path \"$K\" \\( {_ODD_NAME_TEST} -exec {_ODD_NAME_SCAN} -o -print \\) 2>/dev/null "
        f"| {_CAP_LIST}; "
        'cat "$X"; '
        f"if [ -s \"$L\" ]; then {scan}; "
        f"else echo 'Scanned files: 0'; R=0; fi; "
        + _ODD_NAME_MERGE
        + _SKIPPED_REPORT
        + f'rm -f "$L" "$X" "$Y" "$K"; echo "{mark}$R"'
    )


# --- Sperre je Server und Zeitlimit auf dem Server ----------------------------------------------------------
# Jeder clamscan lädt die ganze Datenbank (rund 1 GB); laufen Wächter und Schnellscan zugleich, kann der Server hängen
# bleiben. Darum nimmt jeder Lauf zuerst eine Sperre (`flock`): der Wächter nur, wenn sie frei ist, ein Scan wartet.

# `flock` öffnet die Sperrdatei mit `O_CREAT` und folgt Verknüpfungen; in einem für andere beschreibbaren Ordner
# (`/tmp`) könnte jemand die Datei vorher anlegen, einen Link setzen oder die Sperre festhalten und so den Wächter
# abschalten. Darum ein eigener Ordner (Rechte 700, gehört dem Benutzer); sonst läuft der Scan ohne Sperre.
LOCK_DIR_ROOT = "/run/nodvard-shield"
LOCK_DIR_USER = ".nodvard-shield"
LOCK_FILE = "scan.lock"
# Ohne root trägt der Dateiname die Kennung des Rechners: Auf NFS ist der Heimatordner auf allen Servern derselbe (Linux
# reicht `flock` an den NFS-Server weiter), mit festem Namen setzte der Wächter überall aus, sobald irgendwo ein Scan
# lief. Beide Teile, weil geklonte VMs oft dieselbe machine-id, frische Installationen denselben Rechnernamen haben.
_LOCK_FILE_USER = (
    # Jedes Werkzeug mit eigenem `2>/dev/null`: Fehlt eins (sehr kleine Systeme), meldet die Shell sonst "not found", und das
    # landete in der Ausgabe des Scans. Der Name ist dann eben kürzer (`scan--.lock`), die Sperre gilt trotzdem.
    "scan-$(uname -n 2>/dev/null | tr -cd 'A-Za-z0-9._-' 2>/dev/null | cut -c1-64 2>/dev/null)"
    "-$(cat /etc/machine-id 2>/dev/null | tr -cd 0-9a-f 2>/dev/null | cut -c1-32 2>/dev/null).lock"
)
# Zeitlimit auf dem Server: Bricht die Verbindung ab oder läuft das Dashboard-Limit ab, läuft der Befehl dort sonst
# weiter (und hält die Sperre). `timeout` beendet ihn vorher: TERM nach Limit minus 45 Sekunden, KILL 15 Sekunden
# später, also 30 Sekunden vor dem Dashboard-Limit. So kommt die Zeitüberschreitung noch an.
KILL_AFTER_S = 15
TIMEOUT_MARGIN_S = 45


def server_timeout_s(limit_s: int) -> int:
    """Sekunden, nach denen `timeout` auf dem Server den Lauf beendet (immer unter dem Limit des Dashboards)."""
    limit_s = int(limit_s)
    return limit_s - TIMEOUT_MARGIN_S if limit_s > 2 * TIMEOUT_MARGIN_S else max(1, limit_s // 2)


def guard_command(
    command: str, *, mark: str, limit_s: int, wait_s: int | None = None, root_dir: str = LOCK_DIR_ROOT,
) -> str:
    """Führt `command` (dieselbe `mark`) unter der Sperre des Servers und mit Zeitlimit aus.

    Ausgabe wie `command`, dazu ggf. `@@scan-busy-<hex>` (Sperre belegt) oder `@@scan-timeout-<hex>` (Zeitlimit)."""
    mark = _checked_mark(mark)
    limit = server_timeout_s(limit_s)
    # `wait_s=None` (Wächter) gibt sofort auf, sonst wartet der Lauf bis `wait_s` (zählt zum Zeitlimit); bei belegter
    # Sperre läuft `command` gar nicht. Das Zeitlimit (Code 124, 137 nach KILL) lässt die Marke von `command` fehlen.
    # Ohne `flock` (BusyBox, NAS) oder bei anderem Fehler als "belegt": ohne Sperre; ohne `timeout -k`: ohne Limit.
    take = "flock -n 9" if wait_s is None else f"flock -w {max(1, min(int(wait_s), limit // 2))} 9"
    busy, late = _run_label(mark, "busy"), _run_label(mark, "timeout")
    # `root_dir`: der Ordner der Sperre für root; nur Tests setzen ihn anders als `LOCK_DIR_ROOT`.
    return (
        f'D=; N={LOCK_FILE}; if [ "$(id -u)" -eq 0 ]; then D=' + _q(root_dir) + "; "
        'elif [ -n "${HOME:-}" ] && [ "$HOME" != / ]; then D="$HOME/' + LOCK_DIR_USER + f'"; N="{_LOCK_FILE_USER}"; fi; '
        'F=; case "$(flock --version 2>/dev/null)" in *util-linux*) '
        'if [ -n "$D" ]; then mkdir -m 700 "$D" 2>/dev/null; '
        'if [ -d "$D" ] && [ ! -L "$D" ] '
        '&& case "$(stat -c %u:%a "$D" 2>/dev/null)" in "$(id -u):700"|"$(id -u):2700") true;; *) false;; esac '
        '&& (: >> "$D/$N") 2>/dev/null; then F="$D/$N"; fi; fi;; esac; '
        f"P=; [ -z \"$F\" ] || P='{take}; [ $? -ne 1 ] || {{ echo \"{busy}\"; exit 0; }}; '; "
        'T=; case "$(timeout --version 2>/dev/null)" in *coreutils*) '
        f'timeout -k 1 5 sh -c : 2>/dev/null && T="timeout -k {KILL_AFTER_S} {limit}";; esac; '
        f'$T sh -c "$P"{_q(command)} 9>>"${{F:-/dev/null}}"; R=$?; '
        f'if [ -n "$T" ] && {{ [ "$R" -eq 124 ] || [ "$R" -eq 137 ]; }}; then echo "{late}"; fi'
    )


_HELPER_MAX_NUMBER = 999_999
"""Größte Zahl, die das Hilfsskript bei `scan` und `watch` annimmt (sechs Stellen)."""


def _helper_scan_ok(paths: list[str], mark: str | None, numbers: tuple[int, ...]) -> bool:
    """Passen die Angaben zu dem, was `shield-root scan`/`watch` annimmt? Sonst bleibt es beim alten Weg."""
    return (
        bool(paths) and all(isinstance(p, str) and p.startswith("/") and "\x00" not in p for p in paths)
        and isinstance(mark, str) and _RC_MARK_RE.fullmatch(mark) is not None
        and all(1 <= n <= _HELPER_MAX_NUMBER for n in numbers)
    )


def scan_helper_args(
    paths: list[str], *, max_filesize_mb: int, exclude: list[str] | None, mark: str, limit_s: int, wait_s: int,
) -> list[str] | None:
    """Derselbe Lauf wie `guard_command(build_scan_command(...), ...)` mit denselben Angaben als Aufruf von
    `shield-root scan` (ab Version 6), oder `None`. Die Ausnahmen gehen fertig als Ausdrücke für `--exclude-dir` mit."""
    numbers = (int(limit_s), int(wait_s), int(max_filesize_mb))
    if not _helper_scan_ok(paths, mark, numbers):
        return None
    regexes = [r for r in (scan_exclude_regex(p) for p in [*_ALWAYS_EXCLUDED, *(exclude or [])]) if r]
    return ["scan", mark, *map(str, numbers), *regexes, "--", *paths]


def watch_helper_args(
    paths: list[str], *, minutes: int, max_filesize_mb: int, use_clamd: bool, mark: str,
    skip_folders: list[str] | None, limit_s: int,
) -> list[str] | None:
    """Derselbe Lauf wie `guard_command(build_watch_command(...), ...)` (mit dem Standard für `exclude`) als Aufruf
    von `shield-root watch` (ab Version 6), oder `None`. Die Ordner gehen fertig als Muster für `find -path` mit."""
    numbers = (int(limit_s), int(minutes), int(max_filesize_mb))
    if not _helper_scan_ok(paths, mark, numbers):
        return None
    skips = [_find_literal(f) for f in (exclude_folder(e) for e in (skip_folders or [])) if f is not None]
    return ["watch", mark, *map(str, numbers), "1" if use_clamd else "0", *skips, "--", *paths]


_FOUND_RE = re.compile(r"^(?P<path>/.*): (?P<sig>.+) FOUND$")
_SCANNED_RE = re.compile(r"^Scanned files: ([0-9]+)")
_INFECTED_RE = re.compile(r"^Infected files: ([0-9]+)")
# Zeile, die der Wächter-Befehl selbst am Ende schreibt, wenn er Dateien mit Zeilenumbruch im Namen separat geprüft hat
# (`_ODD_NAME_MERGE`): Kein Dateiname steht dahinter, nur die Zahl aus der Shell.
_ODD_NAMES_RE = re.compile(r"^" + re.escape(_ODD_NAME_LABEL) + r": ([0-9]+)$")
# Zeile des Befehls mit dem Rest über der Obergrenze des Wächters (`_SKIPPED_REPORT`): ebenfalls nur die Zahl aus der Shell.
_SKIPPED_RE = re.compile(r"^" + re.escape(_SKIPPED_LABEL) + r": ([0-9]+)$")
# Meldungen über eine nicht lesbare Datei (Formate aus dem ClamAV-Quelltext 0.103 bis 1.4, der Pfad steht roh darin).
# clamdscan: "ERROR: Can't access file <Pfad>"; clamscan: "WARNING: Can't open file <Pfad>: <Grund>" (Code 2).
_ACCESS_ERROR_RE = re.compile(r"^(?:ERROR|WARNING): Can.t (?:access|open) (?:file|directory):? (?P<path>.+)$")
# clamscan, Eintrag der --file-list fehlt (zerrissener Name): erst "<Pfad>: <Grund>" (perror), dann diese Zeile, Code 2.
_ACCESS_WARNING_TAIL_RE = re.compile(r"^WARNING: (?P<path>.+): Can.t access file$")
# clamd (über clamdscan): "<Pfad>: <Grund>. ERROR".
_ACCESS_ERROR_TAIL_RE = re.compile(r"^(?P<path>/.*): [^:]*\. ERROR$")


def _is_access_message(line: str) -> bool:
    """Eine Meldung über eine Datei, die ClamAV nicht lesen konnte (kein Hinweis auf einen kaputten Lauf)."""
    return _ACCESS_ERROR_RE.match(line) is not None or _ACCESS_WARNING_TAIL_RE.match(line) is not None


# "Verschwunden" (mit clamscan 1.0.5 und 1.5.4 nachgeprüft): "WARNING: <Pfad>: Can't access file" gilt nur so, wenn die
# Zeile davor (`perror`) "No such file or directory" oder "Not a directory" nennt; "Input/output error", "Permission
# denied" oder keine Zeile davor heißen: Datei noch da, nicht geprüft.
_GONE_REASONS = ("No such file or directory", "Not a directory")
# Zwischen Prüfung und Öffnen verschwunden (zählt bei "Total errors" mit) oder keine normale Datei mehr (Pipe o. Ä.).
_GONE_OPEN_RE = re.compile(r"^WARNING: Can.t open file (?P<path>.+): (?:No such file or directory|Not a directory)$")
_UNSUPPORTED_RE = re.compile(r"^WARNING: (?P<path>.+): Not supported file type$")
# Ohne Leserecht meldet clamscan "Can't open file <Pfad>: Permission denied", einen nicht lesbaren Ordner mit `-i` gar
# nicht: dann steht nur "Total errors: N" in der Zusammenfassung.
_TOTAL_ERRORS_RE = re.compile(r"^Total errors: ([0-9]+)$")
# Die Zusammenfassung von ClamAV (Kopfzeile und alle Zeilen darunter, clamscan 0.103 bis 1.5): nie der Grund eines
# Fehlers. Bei clamscan ist "End Date:" immer die letzte Zeile und würde sonst als Grund gewählt.
_SUMMARY_LINE_RE = re.compile(
    r"^(?:-+ SCAN SUMMARY -+|(?:Known viruses|Engine version|Scanned directories|Scanned files|Infected files|Total errors"
    r"|Not removed|Not moved|Not copied|Data scanned|Data read|Time|Start Date|End Date):.*)$"
)


def _has_text(line: str) -> bool:
    """Steht hinter dem ersten Doppelpunkt ein Wort (nicht nur Sternchen wie im Rahmen der Alt-Datenbank-Warnung)?"""
    return re.search(r"[A-Za-z]", line.split(":", 1)[-1]) is not None


def _failure_reason(
    lines: list[str], rc: int, *, unread: int = 0, gone_lines: frozenset[str] | set[str] = frozenset(),
    perror: dict[str, list[str]] | None = None,
) -> str:
    """Der Grund für einen gescheiterten Lauf, aus den (nicht leeren) Zeilen der Ausgabe."""
    # Rangfolge: Fehlerzeile; Warnung von clamscan (außer "verschwunden"); Fehler laut Zusammenfassung; "verschwunden";
    # Bibliothekswarnung; letzte fremde Zeile; Satz mit dem Code.
    perror = perror or {}
    for line in lines:
        if line.startswith(("ERROR:", "LibClamAV Error")) and _has_text(line):
            return line
    for line in lines:
        if line.startswith("WARNING:") and line not in gone_lines and _has_text(line):
            # Bei "Can't access file" steht der Grund in der Zeile davor (`perror`).
            tail = _ACCESS_WARNING_TAIL_RE.match(line)
            reasons = [r for r in perror.get(tail.group("path"), []) if r not in _GONE_REASONS] if tail else []
            return f"{tail.group('path')}: {reasons[0]}" if tail and reasons else line
    if unread > 0:
        # Einen nicht lesbaren Ordner meldet clamscan mit `-i` sonst gar nicht.
        return (f"ClamAV endete mit Code {rc}. Laut Zusammenfassung konnte es {unread} Datei(en) oder Ordner nicht lesen, "
                "oft fehlen dafür die Rechte.")
    # Bibliothekswarnungen (etwa "Datenbank älter als 7 Tage") stehen beim Laden vor allem anderen, fast nie die Ursache.
    for prefix in ("WARNING:", "LibClamAV Warning"):
        for line in lines:
            if line.startswith(prefix) and _has_text(line):
                return line
    for line in reversed(lines):
        own = line.startswith("@@") or _ODD_NAMES_RE.match(line) or _SKIPPED_RE.match(line)
        if not own and not _SUMMARY_LINE_RE.match(line) and not line.startswith(("WARNING:", "LibClamAV Warning")):
            return line
    return f"ClamAV endete mit Code {rc} ohne Fehlermeldung."


def duration_text(seconds: float) -> str:
    """„20 Sekunden“, „1 Minute“, „5 Minuten“, „4 Stunden“ (volle Minuten und Stunden, wo es aufgeht)."""
    total = round(seconds)
    if total >= 3600 and total % 3600 == 0:
        hours = total // 3600
        return "1 Stunde" if hours == 1 else f"{hours} Stunden"
    if total >= 60 and total % 60 == 0:
        minutes = total // 60
        return "1 Minute" if minutes == 1 else f"{minutes} Minuten"
    return f"{total} Sekunden"


def timeout_message(limit_s: int | None = None) -> str:
    """Text, wenn `timeout` auf dem Server den Lauf beendet hat; `limit_s` ist das Dashboard-Zeitlimit dieser Art."""
    when = f" nach etwa {duration_text(limit_s)}" if limit_s else ""
    return f"Zeitüberschreitung: Der Scan hat zu lange gebraucht und wurde{when} auf dem Server beendet."


BUSY_MESSAGE = (
    "Scan nicht gestartet: Auf dem Server lief noch ein anderer Scan und wurde nicht rechtzeitig fertig. "
    "Versuch es später noch einmal."
)
CONNECTION_LOST_MESSAGE = "Scan fehlgeschlagen: Die Verbindung zum Server ist abgebrochen, bevor der Scan fertig war."


def _has_label(lines: list[str], mark: str | None, word: str) -> bool:
    """Steht die Zeile `@@scan-<word>-<hex>` (`guard_command`) in der Ausgabe? Ohne `mark` zählt jede Zufallszahl."""
    if mark is not None:
        return _run_label(_checked_mark(mark), word) in lines
    return any(re.fullmatch(rf"@@scan-{word}-[0-9a-f]{{16}}", line) for line in lines)


# Der Grund hinter dem Pfad bei `perror`: ein Satz aus Buchstaben ("No such file or directory", "Permission denied").
_ERRNO_REASON_RE = re.compile(r"^[A-Z][a-z][A-Za-z ,'-]*$")
# Die Meldung der Shell selbst ("sh: 1: clamscan: not found", "bash: line 1: clamscan: command not found").
_NOT_INSTALLED_RE = re.compile(r"^(?:/(?:usr/)?bin/)?(?:ba|da|a)?sh: (?:(?:line )?[0-9]+: )?clamscan: (?:command )?not found$")
NOT_INSTALLED_MESSAGE = "ClamAV ist auf diesem Server nicht installiert."
# Fester Anfang des Hinweises zur Obergrenze: Daran erkennt der Defender später eine nur deswegen unvollständige
# Scan-Zeile (die Zahl dahinter wechselt von Lauf zu Lauf).
SKIPPED_PREFIX = f"Der Wächter hat nur {WATCH_MAX_FILES} von "


def skipped_message(skipped: int, *, moved: bool = False) -> str:
    """Hinweis, wenn der Wächter wegen der Obergrenze nicht alle neuen Dateien geprüft hat."""
    text = f"{SKIPPED_PREFIX}{WATCH_MAX_FILES + skipped} neuen Dateien geprüft, der Rest blieb ungeprüft."
    if moved:
        text += " Es wurde nichts automatisch verschoben."
    return text + " Bitte einen Schnell- oder Tiefenscan starten."


AMBIGUOUS_OUTPUT_MESSAGE = (
    "Scan fehlgeschlagen: ClamAV meldet Dateien, die nicht zu den gescannten Ordnern passen (zum Beispiel wegen "
    "eines Zeilenumbruchs im Dateinamen). Bitte auf dem Server nachsehen."
)


def _absolute(path: str) -> str | None:
    """Der Pfad ohne `.`/`..`/doppelte Schrägstriche (nur der Text, kein Dateizugriff); `None`, wenn er nicht absolut ist."""
    if not path.startswith("/"):
        return None
    return "/" + posixpath.normpath(path).lstrip("/")


def _under_roots(path: str, roots: list[str]) -> bool:
    """Liegt `path` (ohne `..` aufgelöst) in einer der Wurzeln, die der Befehl gescannt hat?"""
    norm = _absolute(path)
    if norm is None:
        return False
    for root in roots:
        base = _absolute(root)
        if base is not None and (base == "/" or norm == base or norm.startswith(base + "/")):
            return True
    return False


def _split_resolved_roots(body: str, roots: list[str], mark: str | None) -> tuple[list[str], str]:
    """Liest die ersten `len(roots)` Zeilen (`_resolve_roots_command`) und gibt die brauchbaren Pfade plus den Rest zurück.

    Fehlt eine dieser Zeilen, stammt die Ausgabe nicht von diesem Befehl: keine aufgelösten Wurzeln, Ausgabe unverändert."""
    label = _roots_label(mark) if mark is not None else None
    lines = body.split("\n")
    if not roots or len(lines) < len(roots):
        return [], body
    resolved: list[str] = []
    for root, raw in zip(roots, lines, strict=False):
        line = raw.rstrip("\r")
        if label is not None:
            if not line.startswith(label):
                return [], body
            path = line[len(label):]
        else:
            m = re.match(r"^@@scan-roots-[0-9a-f]{16}=(.*)$", line)
            if m is None:
                return [], body
            path = m.group(1)
        norm = _absolute(path) if path and "\r" not in path else None
        # Nie `/` als aufgelöste Wurzel, außer sie ist selbst eingetragen: Ein Link auf `/` weitete die Prüfung aus.
        if norm is not None and (norm != "/" or _absolute(root) == "/"):
            resolved.append(norm)
    return resolved, "\n".join(lines[len(roots):])


def parse_scan_output(
    output: str, mark: str | None = None, roots: list[str] | None = None, limit_s: int | None = None,
) -> ScanResult:
    """Wertet die Ausgabe von `build_scan_command` / `build_watch_command` aus.

    Sie enthält Dateinamen vom Server, ClamAV schreibt sie unverändert hin: Ein Name darf die Auswertung nie steuern."""
    # ClamAV 1.0.5 gibt Zeilenumbruch, Wagenrücklauf, Seitenvorschub und ESC im Namen roh aus. Es zählt der letzte
    # Treffer der Marke am Zeilenanfang (die echte steht ganz am Ende, ein Dateiname mit Marke davor versteckt nichts);
    # alles nach ihr fliegt raus. Fehlt die Marke, wurde der Lauf nicht ordentlich beendet: Fehler, nie "sauber".
    pattern = _RC_MARK_RE.pattern if mark is None else re.escape(_checked_mark(mark))
    matches = list(re.finditer(r"^" + pattern + r"([0-9]+)[ \t\r]*$", output, re.MULTILINE))
    rc_match = matches[-1] if matches else None
    rc = int(rc_match.group(1)) if rc_match else None
    body = output[: rc_match.start()] if rc_match else output

    if rc == 127:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)

    # `roots`: die gescannten Pfade (ohne Angabe keine Prüfung). Bei Symlinks melden ClamAV und `clamdscan` Funde mit
    # dem aufgelösten Pfad (`_resolve_roots_command`); es gelten die eingetragene und die aufgelöste Wurzel.
    check_roots: list[str] | None = None
    # Aufgelöst wird nur die Wurzel selbst (ein Link darin nach `/etc` macht einen Fund in `/etc` nicht sicher). Grenze:
    # Wer den Elternordner einer Wurzel beschreiben darf, kann sie auf einen anderen Ordner (nicht `/`) umlenken; das
    # sind aber von ClamAV aufgelöste Pfade, keine Zeilen aus einem Dateinamen.
    if roots is not None:
        resolved, body = _split_resolved_roots(body, roots, mark)
        check_roots = [*roots, *resolved]

    findings: list[tuple[str, str]] = []
    errors: list[str] = []
    found_lines = 0  # Zeilen, die auf " FOUND" enden (auch unlesbare und eingeschmuggelte)
    stray = 0  # Zeilen mit Pfad am Anfang, die kein Fund sind
    files: int | None = None
    summary_infected: int | None = None
    odd_names = 0
    skipped = 0
    access_errors: list[str] = []  # Pfade aus "kann Datei nicht lesen"-Meldungen
    gone_paths: list[str] = []  # Pfade aus Meldungen "Datei verschwunden / keine normale Datei mehr"
    gone_lines: set[str] = set()  # die Meldungen selbst (für den Grund eines Fehlers)
    gone_open = 0  # davon "Can't open file ...: No such file or directory" (zählt bei "Total errors" mit)
    perror: dict[str, list[str]] = {}  # Pfad -> Gründe aus den `perror`-Zeilen von clamscan ("<Pfad>: <Grund>")
    tails: list[tuple[str, str]] = []  # "WARNING: <Pfad>: Can't access file" (Zeile, Pfad): erst mit `perror` entschieden
    blocking = 0  # Fehler- und Warnzeilen, die nicht sagen, dass eine Datei verschwunden ist
    total_errors = 0
    shell_not_found = False
    nonblank: list[str] = []
    # Nur am Zeilenvorschub trennen, nicht mit `str.splitlines()` (trennt auch bei CR, Seitenvorschub, U+0085, U+2028
    # ...): Eine Fund-Zeile mit so einem Zeichen im Namen fiele auseinander (Fund unsichtbar, Lauf "sauber").
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        nonblank.append(line)
        is_found_line = line.endswith(" FOUND")
        found_lines += int(is_found_line)
        # Die Regex nur auf Zeilen, die wie ein Fund enden: Auf langen Bruchstücken mit vielen ": " (Dateiname mit
        # Zeilenumbruch) setzte sie sonst quadratisch zurück und blockierte das Dashboard sekundenlang.
        m = _FOUND_RE.match(line) if is_found_line else None
        if m:
            findings.append((m.group("path"), m.group("sig")))
        elif line.startswith("/"):
            # Mit `-i` schreibt ClamAV Pfade nur in Fund-Zeilen; der Anfang eines Fundes mit Zeilenumbruch im Namen
            # ("/tmp/q") beginnt immer mit dem Pfad des gescannten Ordners.
            stray += 1
            shell_not_found = shell_not_found or bool(_NOT_INSTALLED_RE.match(line))
            if line.endswith(" ERROR"):
                if (tail := _ACCESS_ERROR_TAIL_RE.match(line)) is not None:
                    access_errors.append(tail.group("path"))
            elif not is_found_line:
                # clamscan: `perror` vor "WARNING: <Pfad>: Can't access file" ("<Pfad>: No such file or directory")
                head, sep, reason = line.rpartition(": ")
                if sep:
                    perror.setdefault(head, []).append(reason)
                if sep and _ERRNO_REASON_RE.match(reason):
                    access_errors.append(head)
                    blocking += int(reason not in _GONE_REASONS)
        elif line.startswith("WARNING:") and (
            (warned := _ACCESS_WARNING_TAIL_RE.match(line) or _ACCESS_ERROR_RE.match(line)) is not None
        ):
            errors.append(line)  # wie bei "ERROR: Can't access file": sichtbar, aber kein Fehlschlag
            access_errors.append(warned.group("path"))
            if warned.re is _ACCESS_WARNING_TAIL_RE:
                tails.append((line, warned.group("path")))
            elif (gone := _GONE_OPEN_RE.match(line)) is not None:
                gone_paths.append(gone.group("path"))
                gone_lines.add(line)
                gone_open += 1
            else:
                blocking += 1
        elif (unsupported := _UNSUPPORTED_RE.match(line)) is not None:
            access_errors.append(unsupported.group("path"))
            gone_paths.append(unsupported.group("path"))
            gone_lines.add(line)
        elif line.startswith("WARNING:"):
            blocking += 1  # eine andere Warnung von ClamAV: kein Beleg dafür, dass nur Dateien verschwunden sind
        elif line.startswith(("ERROR:", "LibClamAV Error")):
            blocking += 1
            errors.append(line)
            if (access := _ACCESS_ERROR_RE.match(line)) is not None:
                access_errors.append(access.group("path"))
        elif (summary := _SCANNED_RE.match(line)) is not None:
            files = int(summary.group(1))  # der letzte Treffer gilt: die echte Zusammenfassung steht nach allen Namen
        elif (summary := _INFECTED_RE.match(line)) is not None:
            summary_infected = int(summary.group(1))
        elif (summary := _ODD_NAMES_RE.match(line)) is not None:
            odd_names = int(summary.group(1))  # steht als Letztes vor der Marke, nach allen Namen
        elif (summary := _SKIPPED_RE.match(line)) is not None:
            skipped = int(summary.group(1))  # ebenso: die letzte Zeile gilt, die echte steht direkt vor der Marke
        elif (summary := _TOTAL_ERRORS_RE.match(line)) is not None:
            total_errors = int(summary.group(1))
        else:
            shell_not_found = shell_not_found or bool(_NOT_INSTALLED_RE.match(line))
    # "Can't access file" heißt nur "verschwunden", wenn clamscan es für genau diesen Pfad davor so begründet hat; jeder
    # andere oder fehlende Grund: Die Datei kann noch da sein und wurde nicht geprüft.
    for line, path in tails:
        if perror.get(path) and all(reason in _GONE_REASONS for reason in perror[path]):
            gone_paths.append(path)
            gone_lines.add(line)
        else:
            blocking += 1

    # Code 1 heißt mindestens ein Fund, auch wenn keine Zeile lesbar war. "Nicht installiert" gilt nur als ganze Zeile
    # der Shell oder bei Code 127, nie als Teilstück eines Dateinamens.
    infected_count = max(len(findings), summary_infected or 0, 1 if rc == 1 else 0)
    if shell_not_found and not infected_count:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)
    # Nur die Zusammenfassung von ClamAV selbst (ohne Sonderdateien) zeigt, dass der Lauf bis zum Ende kam; die Zahl der
    # Sonderdateien (`Unusual file names: N`) stammt von der Shell, ihre Fund-Zeilen stehen roh in der Ausgabe.
    summary_files = files
    if odd_names:
        files = (files or 0) + odd_names

    # Pfade, die nicht zu den gescannten Wurzeln passen, stammen aus einem Dateinamen (oder einem Bruchstück davon):
    # Fund und Lauf sind `unreliable`, ein Lauf mit Code 2 gilt nicht als sauber. Zweite Absicherung hinter der
    # Zusammenfassung; im clamd-Modus ohne Zusammenfassung die einzige.
    foreign = check_roots is not None and (
        any(not _under_roots(path, check_roots) for path, _sig in findings)
        or any(not _under_roots(path, check_roots) for path in access_errors)
    )
    # Nicht alle neuen Dateien geprüft (Obergrenze des Wächters): nie "sauber", nie automatisch verschieben.
    incomplete = skipped_message(skipped) if skipped else None
    # Code 2 ohne geprüfte Datei ist nur sauber, wenn ClamAV für jede Datei meldet, dass sie verschwunden oder keine
    # normale Datei mehr ist (beim Wächter Alltag), "Total errors" dazu passt und keine Wurzel betroffen ist. Sonst
    # (fehlende Leserechte oder Wurzel, nicht ladbare Datenbank mit 0 Dateien) ist es ein Fehler.
    root_paths = {_absolute(root) for root in check_roots or []}
    nothing_left = (
        rc == 2 and summary_files == 0 and summary_infected is not None and not odd_names and check_roots is not None
        and bool(gone_paths) and not blocking and total_errors <= gone_open
        and not any(_absolute(path) in root_paths for path in gone_paths)
    )

    if infected_count:
        # Mit Zeilenumbruch im Dateinamen zerfällt ein Fund in mehrere Zeilen, die letzte kann wie ein Fund für einen
        # anderen Pfad aussehen. `unreliable` sind Läufe mit Pfad am Zeilenanfang ohne Fund, Fund-Zeilen, die nicht zu
        # `Infected files:` passen, Sonderdateien oder Pfaden außerhalb der Wurzeln; ein Fund bleibt ein Fund.
        paths_unsure = (
            rc is None or stray > 0 or len(findings) != found_lines
            or (summary_infected is not None and summary_infected != found_lines)
            or len(findings) < infected_count
            or odd_names > 0 or foreign
        )
        return ScanResult(
            status="infected", files_scanned=files, findings=findings, errors=errors,
            infected=infected_count, unreliable=paths_unsure or skipped > 0, paths_unsure=paths_unsure,
            skipped=skipped,
        )
    if rc == 0:
        if incomplete:
            return ScanResult(status="error", files_scanned=files, errors=errors, error=incomplete,
                              unreliable=True, skipped=skipped)
        return ScanResult(status="clean", files_scanned=files, errors=errors)
    if rc == 2 and (summary_files or nothing_left):
        # `files` zählt auch die Sonderdateien (Zahl von der Shell) und beweist bei Code 2 nichts: Eine kaputte
        # Datenbank plus eine Datei mit Zeilenumbruch im Namen sähe sonst nach einem Lauf über eine Datei aus. Darum
        # zählt nur die ClamAV-Zusammenfassung; mit Sonderdateien nur Meldungen über nicht lesbare Dateien.
        other_errors = [e for e in errors if not _is_access_message(e)]
        if odd_names and other_errors:
            return ScanResult(status="error", files_scanned=files, errors=errors,
                              error=f"Scan fehlgeschlagen: {other_errors[0]}")
        if foreign:
            # "Kann Datei nicht lesen" für einen Pfad, den dieser Lauf nicht gescannt hat: das Bruchstück eines
            # Dateinamens mit Zeilenumbruch. Dahinter steckt eine Datei, die nie geprüft wurde: nicht "sauber".
            return ScanResult(status="error", files_scanned=files, errors=errors, error=AMBIGUOUS_OUTPUT_MESSAGE)
        # Einzelne Dateien/Ordner nicht lesbar (meist fehlende Rechte, oder zwischen `find` und Scan verschwunden), der
        # Rest ist sauber; die Meldungen bleiben in `errors` sichtbar.
        if incomplete:
            return ScanResult(status="error", files_scanned=files, errors=errors, error=incomplete,
                              unreliable=True, skipped=skipped)
        return ScanResult(status="clean", files_scanned=files, errors=errors)
    if rc is None:
        # Die Zeilen von `guard_command` zählen nur, wenn die Marke fehlt. Funde bleiben in jedem Fall Funde.
        if _has_label(nonblank, mark, "busy"):
            return ScanResult(status="error", errors=errors, error=BUSY_MESSAGE, busy=True)
        if _has_label(nonblank, mark, "timeout"):
            return ScanResult(status="error", files_scanned=files, errors=errors, error=timeout_message(limit_s),
                              timed_out=True)
        # Die Marke fehlt: Meist ist die SSH-Verbindung mitten im Lauf abgerissen.
        return ScanResult(status="error", files_scanned=files, errors=errors, error=CONNECTION_LOST_MESSAGE)
    detail = _failure_reason(nonblank, rc, unread=total_errors - gone_open, gone_lines=gone_lines, perror=perror)
    return ScanResult(status="error", files_scanned=files, errors=errors, error=f"Scan fehlgeschlagen: {detail}")


STATUS_COMMAND = (
    # Die Abfrage läuft ohne root. Debian und Raspberry Pi OS legen Lynis nach /usr/sbin, das ein normaler Benutzer
    # nicht im Suchpfad hat: Ohne diese Zeile gälte Lynis nach der Installation weiter als "nicht installiert".
    "PATH=\"$PATH:/usr/local/sbin:/usr/sbin:/sbin\"; "
    "echo @@clam; (clamscan --version 2>/dev/null || echo none); "
    # Kein `|| echo unknown`: Bei einem gestoppten Dienst gibt systemctl "inactive" mit rc 3 aus, dahinter stünde sonst
    # noch "unknown", und "aus" würde nie erkannt.
    "echo @@fresh; (systemctl is-active clamav-freshclam 2>/dev/null; true); "
    # `lynis --version` endet sofort; `lynis show version` legt erst den Bericht neu an (und räumt die PID-Datei eines
    # laufenden Audits weg).
    "echo @@lynis; (lynis --version 2>/dev/null || echo none); "
    f"echo @@quarantine; (ls -1 {QUARANTINE_DIR} 2>/dev/null | wc -l); "
    "echo @@os; (. /etc/os-release 2>/dev/null && echo \"$ID\"); echo @@end"
)


@dataclass
class ProtectionStatus:
    clamav_installed: bool
    clamav_version: str | None = None
    signature_version: str | None = None
    signature_date: str | None = None
    freshclam_active: bool | None = None
    lynis_installed: bool = False
    lynis_version: str | None = None
    quarantine_files: int = 0
    os_id: str | None = None


def parse_status(output: str) -> ProtectionStatus:
    sections: dict[str, str] = {}
    current = None
    for line in output.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            sections[current] = ""
        elif current:
            sections[current] += line.strip() + "\n"
    clam = sections.get("clam", "").strip()
    # "ClamAV 1.0.7/27410/Wed Sep 24 08:23:12 2026"
    clam_match = re.match(r"ClamAV ([\d.]+)(?:/(\d+)/(.+))?", clam)
    lynis = sections.get("lynis", "").strip()
    # Nur die erste Zeile zählt (active | inactive | failed | ...); leer = unbekannt (kein systemd).
    fresh = next((ln for ln in sections.get("fresh", "").splitlines() if ln), "")
    try:
        qcount = int(sections.get("quarantine", "0").strip() or 0)
    except ValueError:
        qcount = 0
    return ProtectionStatus(
        clamav_installed=bool(clam_match),
        clamav_version=clam_match.group(1) if clam_match else None,
        signature_version=clam_match.group(2) if clam_match else None,
        signature_date=clam_match.group(3).strip() if clam_match and clam_match.group(3) else None,
        freshclam_active=True if fresh == "active" else False if fresh in ("inactive", "failed") else None,
        lynis_installed=bool(lynis) and lynis != "none",
        lynis_version=lynis if lynis and lynis != "none" else None,
        quarantine_files=qcount,
        os_id=sections.get("os", "").strip() or None,
    )


SYMLINK_REFUSED = "Die Datei ist nur eine Verknüpfung auf eine andere Datei – aus Sicherheitsgründen nicht verschoben."
PATH_LINK_REFUSED = "Im Pfad der Datei steckt eine Verknüpfung auf einen anderen Ordner – aus Sicherheitsgründen nicht verschoben."
RESTORE_LINK_REFUSED = ("Im Pfad des Ursprungsorts steckt eine Verknüpfung auf einen anderen Ordner – "
                        "aus Sicherheitsgründen nicht wiederhergestellt.")
SYMLINK_SWAPPED = ("Die Datei wurde während der Quarantäne gegen eine Verknüpfung ausgetauscht – "
                   "die Verknüpfung wurde entfernt, die Datei dahinter nicht angefasst.")
HARDLINK_REFUSED = ("Die Datei hat noch weitere Namen (Hardlinks) – aus Sicherheitsgründen nicht verschoben. "
                    "Bitte auf dem Server nachsehen.")
HARDLINK_SWAPPED = ("Die Datei wurde während der Quarantäne gegen einen weiteren Namen einer anderen Datei "
                    "(Hardlink) ausgetauscht – der Name wurde entfernt, ihre Rechte wurden nicht verändert.")


ORIGIN_DIR_NAME = ".herkunft"
"""Unterordner der Quarantäne mit Rechten und Ursprungsort je Datei (erste Zeile die Rechte, dann der Pfad)."""
CHECK_DIR_NAME = ".pruefen"
"""Unterordner der Quarantäne, in dem das Hilfsskript eine Datei vor dem Verschieben mit ClamAV prüft (nur root)."""
QUARANTINE_DIR_UNSAFE = ("Der Ordner der Quarantäne auf dem Server ist eine Verknüpfung, gehört nicht root oder ist für "
                         "andere offen (nicht 700) – aus Sicherheitsgründen nichts verschoben. Bitte "
                         f"{QUARANTINE_DIR} und seine Unterordner auf dem Server prüfen.")


def origin_record(quarantine_path: str) -> str:
    """Wo Rechte und Ursprungsort der Datei `quarantine_path` liegen (neben ihr, im Unterordner `.herkunft`)."""
    folder, _, name = quarantine_path.rpartition("/")
    return f"{folder}/{ORIGIN_DIR_NAME}/{name}"


QUARANTINE_HELPER_MISSING = ("In dieser Installation fehlt das mitgelieferte Hilfsskript von Nodvard Shield, ohne seine "
                             "Prüfung wird nichts verschoben. Bitte Nodvard Deck neu einspielen (docker compose pull, "
                             "dann docker compose up -d).")
_QUARANTINE_FUNCTIONS = ("die", "check_qname", "confirm_finding", "mode4", "split_path", "qdir_safe", "put_back",
                         "quarantine")
_QNAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


def _helper_functions(names: Sequence[str]) -> str | None:
    """Die Shell-Funktionen `names` wörtlich aus dem mitgelieferten Hilfsskript, `None`, wenn eine fehlt."""
    try:
        text = HELPER_FILE.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    parts = []
    for name in names:
        match = re.search(rf"^{name}\(\) {{\n.*?^}}\n", text, re.MULTILINE | re.DOTALL)
        if match is None:
            return None
        parts.append(match.group(0))
    return "".join(parts)


_QUARANTINE_SH = _helper_functions(_QUARANTINE_FUNCTIONS)


def quarantine_command(path: str, quarantine_name: str) -> str:
    """Wie `quarantine` im Hilfsskript, mit dessen eigenen Funktionen: Verschoben wird nur, was ClamAV am Ort und noch
    einmal im Prüfordner selbst findet; Rechte und Ursprung kommen in die Ablage, die Datei wird unlesbar."""
    # Auch ohne Hilfsskript (Anmeldung als root, volle sudo-Regel) zählt nicht, was in der Datenbank steht: Ein Fund aus
    # einer veränderten Sicherung machte sonst jede Datei per Klick unlesbar.
    _, _, name = path.rpartition("/")
    if not path.startswith("/") or name in ("", ".", ".."):
        raise ValueError("Ungültiger Dateipfad.")
    if not _QNAME_RE.fullmatch(quarantine_name):
        raise ValueError("Ungültiger Name in der Quarantäne.")
    if _QUARANTINE_SH is None:
        raise ValueError(QUARANTINE_HELPER_MISSING)
    return f"set -eu; QDIR={_q(QUARANTINE_DIR)}; {_QUARANTINE_SH}quarantine {_q(path)} {_q(quarantine_name)}"


RESTORE_FOLDER_MISSING = "Der Ursprungsordner ist nicht mehr da oder nicht erreichbar – bitte zuerst auf dem Server wieder anlegen."
RESTORE_TARGET_TAKEN = "Am Ursprungsort liegt inzwischen eine andere Datei oder Verknüpfung – nichts überschrieben, die Datei bleibt in der Quarantäne."
RESTORE_NOT_MOVED = "Die Datei ließ sich nicht zurückverschieben – sie bleibt in der Quarantäne."
RESTORE_STAGE_FAILED = ("Im Ursprungsordner ließ sich kein geschützter Zwischenordner anlegen (zum Beispiel auf einem "
                        "Laufwerk ohne Dateirechte oder in einem schreibgeschützten Ordner) oder er wurde ausgetauscht – "
                        "aus Sicherheitsgründen nicht wiederhergestellt.")
RESTORE_STUCK = "Die Datei ließ sich weder zurücklegen noch in die Quarantäne zurückholen. Sie liegt nur für root lesbar hier:"
RESTORE_NO_ORIGIN = ("Für diese Datei gibt es auf dem Server keinen gültigen Eintrag zu Ort und Rechten (etwa weil "
                     "sie mit Nodvard Deck 0.8 oder älter in die Quarantäne kam). Nodvard Shield legt nur an "
                     "hinterlegte Orte zurück; bitte auf dem Server von Hand zurücklegen oder die Datei löschen.")
QUARANTINE_NOT_FOUND = "ClamAV findet beim erneuten Prüfen nichts in der Datei – sie bleibt, wo sie ist."
QUARANTINE_UNCHECKED = ("Die Datei ließ sich vor dem Verschieben nicht noch einmal prüfen (ClamAV fehlt oder antwortet "
                        "nicht) – sie bleibt, wo sie ist.")
QUARANTINE_CHECK_BUSY = ("Im Prüfordner der Quarantäne liegt noch eine Datei mit diesem Namen von einem abgebrochenen "
                         f"Lauf – nichts verschoben. Bitte auf dem Server unter {QUARANTINE_DIR}/{CHECK_DIR_NAME} nachsehen.")
QUARANTINE_KEPT = ("Die Datei kam nicht in die Quarantäne und ließ sich auch nicht an ihren Ort zurücklegen (dort liegt "
                   "inzwischen etwas anderes, oder sie wurde während des Verschiebens ausgetauscht). Sie liegt nur für "
                   "root zugänglich hier:")

_RESTORE_SH = _helper_functions(("die", "check_qname", "mode4", "split_path", "qdir_safe", "put_back", "restore"))


def restore_command(quarantine_path: str) -> str:
    """Wie `restore` im Hilfsskript, mit dessen eigenen Funktionen: Ort und Rechte nur aus der Ablage der Quarantäne,
    zurück nie über einen Ordner, der inzwischen eine Verknüpfung ist."""
    # Ort und Rechte in der Datenbank können aus einer veränderten Sicherung stammen; root legte die Datei damit
    # etwa nach /etc/profile.d.
    name = _quarantine_name_of(quarantine_path)
    if name is None:
        raise ValueError(_outside_quarantine("stellt Nodvard Shield sie nicht wieder her"))
    if _RESTORE_SH is None:
        raise ValueError(QUARANTINE_HELPER_MISSING)
    return f"set -eu; umask 022; QDIR={_q(QUARANTINE_DIR)}; {_RESTORE_SH}restore {_q(name)}"


_RESTORED_TO_RE = re.compile(r"^@@restored_to=(.*)\nok\n?\Z", re.MULTILINE | re.DOTALL)


def parse_restored_to(output: str) -> str | None:
    """Der Ort, an den `restore` die Datei zurückgelegt hat (aus der Ablage auf dem Server), sonst `None`."""
    match = _RESTORED_TO_RE.search(output)
    return match.group(1) if match else None


QUARANTINE_STATES = ("quarantined", "checking", "absent")
_MODE4_SH = _helper_functions(("mode4",))


def quarantine_state_command(quarantine_name: str) -> str:
    """Nur lesend: liegt die Datei `quarantine_name` in der Quarantäne (`@@state=quarantined`, dazu `@@mode=` aus der
    Ablage), noch im Prüfordner (`checking`) oder nicht dort (`absent`)?"""
    if not _QNAME_RE.fullmatch(quarantine_name):
        raise ValueError("Ungültiger Name in der Quarantäne.")
    if _MODE4_SH is None:
        raise ValueError(QUARANTINE_HELPER_MISSING)
    target = _q(f"{QUARANTINE_DIR}/{quarantine_name}")
    check = _q(f"{QUARANTINE_DIR}/{CHECK_DIR_NAME}/{quarantine_name}")
    origin = _q(origin_record(f"{QUARANTINE_DIR}/{quarantine_name}"))
    return (
        f"{_MODE4_SH}if [ -f {target} ] && [ ! -L {target} ]; then echo '@@state=quarantined'; "
        f"M=; if [ -f {origin} ] && [ ! -L {origin} ]; then M=$(sed -n 1p {origin} 2>/dev/null); fi; "
        "if M=$(mode4 \"$M\"); then echo \"@@mode=$M\"; fi; "
        f"elif [ -e {check} ] || [ -L {check} ]; then echo '@@state=checking'; "
        "else echo '@@state=absent'; fi"
    )


def quarantine_state_args(quarantine_name: str) -> list[str]:
    """Aufruf des Hilfsskripts für dasselbe wie `quarantine_state_command` (prüft den Namen wie dort)."""
    quarantine_state_command(quarantine_name)
    return ["quarantine-state", quarantine_name]


def parse_quarantine_state(output: str) -> str | None:
    match = re.search(r"^@@state=([a-z]+)$", output, re.MULTILINE)
    return match.group(1) if match and match.group(1) in QUARANTINE_STATES else None


def _in_quarantine(quarantine_path: str) -> bool:
    # Der Pfad steht in der Datenbank, auch aus einer eingespielten Sicherung; root fasst sonst beliebige Dateien an.
    # Dieselbe Regel wie im Hilfsskript.
    return _quarantine_name_of(quarantine_path) is not None


def _outside_quarantine(consequence: str) -> str:
    return (
        "Der gespeicherte Ablageort dieser Datei liegt nicht in der Quarantäne (etwa nach einer veränderten Sicherung). "
        f"Aus Sicherheitsgründen {consequence}; bitte auf dem Server unter {QUARANTINE_DIR} nachsehen."
    )


def delete_command(quarantine_path: str) -> str:
    if not _in_quarantine(quarantine_path):
        raise ValueError(_outside_quarantine("löscht Nodvard Shield sie nicht"))
    return f"rm -f {_q(quarantine_path)} {_q(origin_record(quarantine_path))}; echo ok"


# Das Hilfsskript ist reines ASCII und gibt statt eines Satzes `@@msg=<kennung>` aus (bei `restore_stuck` mit dem Ort
# dahinter); `helper_text` setzt denselben Satz ein, den der eigene Befehl des Moduls ausgibt.
HELPER_MESSAGES = {
    "gone": "Datei nicht mehr vorhanden",
    "path_link": PATH_LINK_REFUSED,
    "symlink": SYMLINK_REFUSED,
    "hardlink": HARDLINK_REFUSED,
    "symlink_swapped": SYMLINK_SWAPPED,
    "hardlink_swapped": HARDLINK_SWAPPED,
    "not_in_quarantine": "Nicht mehr in der Quarantäne",
    "restore_folder_missing": RESTORE_FOLDER_MISSING,
    "restore_link": RESTORE_LINK_REFUSED,
    "restore_occupied": "Am Ursprungsort liegt inzwischen eine andere Datei",
    "restore_stage_failed": RESTORE_STAGE_FAILED,
    "restore_not_moved": RESTORE_NOT_MOVED,
    "restore_target_taken": RESTORE_TARGET_TAKEN,
    "restore_stuck": RESTORE_STUCK,
    "restore_no_origin": RESTORE_NO_ORIGIN,
    "quarantine_not_found": QUARANTINE_NOT_FOUND,
    "quarantine_unchecked": QUARANTINE_UNCHECKED,
    "quarantine_dir_unsafe": QUARANTINE_DIR_UNSAFE,
    "quarantine_check_busy": QUARANTINE_CHECK_BUSY,
    "quarantine_kept": QUARANTINE_KEPT,
    "only_apt": "Nur für Debian/Ubuntu (apt)",
    "realtime_no_package": REALTIME_NO_PACKAGE,
    "realtime_no_systemd": REALTIME_NO_SYSTEMD,
    "realtime_not_started": REALTIME_NOT_STARTED,
}
_HELPER_MSG_RE = re.compile(r"^@@msg=([a-z_]+)(?: (.*))?$")


def helper_text(output: str) -> str:
    """Die Ausgabe des Hilfsskripts mit den Sätzen des Moduls statt `@@msg=<kennung>`; andere Zeilen bleiben."""
    lines = []
    for line in output.split("\n"):
        match = _HELPER_MSG_RE.match(line)
        text = HELPER_MESSAGES.get(match.group(1)) if match else None
        if text is None:
            lines.append(line)
        else:
            lines.append(text if match.group(2) is None else f"{text} {match.group(2)}")
    return "\n".join(lines)


def _quarantine_name_of(quarantine_path: str) -> str | None:
    """Der Name in der Quarantäne, wenn der Pfad dort direkt liegt und der Name nur sichere Zeichen hat, sonst `None`."""
    prefix = QUARANTINE_DIR + "/"
    name = quarantine_path[len(prefix):] if quarantine_path.startswith(prefix) else ""
    return name if _QNAME_RE.fullmatch(name) else None


def quarantine_args(path: str, quarantine_name: str) -> list[str]:
    """Aufruf des Hilfsskripts für dasselbe wie `quarantine_command` (prüft die Angaben wie dort)."""
    quarantine_command(path, quarantine_name)
    return ["quarantine", path, quarantine_name]


def restore_args(quarantine_path: str) -> list[str] | None:
    """Aufruf des Hilfsskripts für dasselbe wie `restore_command` (prüft den Pfad wie dort)."""
    restore_command(quarantine_path)
    name = _quarantine_name_of(quarantine_path)
    return None if name is None else ["restore", name]


def delete_args(quarantine_path: str) -> list[str] | None:
    """Aufruf des Hilfsskripts für dasselbe wie `delete_command`; `None`, wenn es dafür nicht taugt."""
    delete_command(quarantine_path)
    name = _quarantine_name_of(quarantine_path)
    return None if name is None else ["delete", name]


def parse_mode(output: str) -> str | None:
    m = re.search(r"@@mode=([0-7]{3,4})", output)
    return m.group(1) if m else None


def quarantine_name(finding_id: str, path: str) -> str:
    # Die Kennung kommt aus der Datenbank (auch aus einer eingespielten Sicherung) und wird Teil des Pfads.
    ident = re.sub(r"[^A-Za-z0-9_-]", "_", finding_id)[:64] or "fund"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", path.rsplit("/", 1)[-1])[:80] or "datei"
    return f"{ident}_{base}"


# --- Lynis --------------------------------------------------------------------

@dataclass
class LynisResult:
    status: str  # ok | error
    hardening_index: int | None = None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    error: str | None = None


LYNIS_DETAIL_LIMIT = 200


def _clean_lynis(entry: str) -> str:
    # Format "ID|Text|Angabe|Lösung|", z. B. "AUTH-9286|Configure maximum password age|-|-|" -> "AUTH-9286: Configure
    # maximum password age". Die Angabe (3. Feld) kommt dazu, sonst wären mehrere SSH-Hinweise wortgleich; sie kann
    # selbst "|" enthalten (Lynis maskiert das nicht), dann ist sie alles zwischen Text und Lösung.
    fields = entry.split("|")
    if len(fields) >= 2 and fields[0] and fields[1] and fields[1] != "-":
        middle = fields[2:-2] if len(fields) >= 5 and fields[-1] == "" else fields[2:3]
        detail = "|".join(middle).strip()
        if detail in ("", "-"):
            detail = ""
        elif len(detail) > LYNIS_DETAIL_LIMIT:
            detail = detail[: LYNIS_DETAIL_LIMIT - 1] + "…"
        return f"{fields[0]}: {fields[1]}" + (f" ({detail})" if detail else "")
    parts = [p for p in fields if p and p != "-"]
    if len(parts) >= 2:
        return f"{parts[0]}: {parts[1]}"
    return entry


def parse_lynis(output: str) -> LynisResult:
    if NO_ROOT in output:
        return LynisResult(status="error", error=no_root_message(output))
    if "@@nolynis" in output:
        return LynisResult(status="error", error="Lynis ist auf diesem Server nicht installiert.")
    warnings, suggestions, index = [], [], None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("warning[]="):
            warnings.append(_clean_lynis(line.split("=", 1)[1]))
        elif line.startswith("suggestion[]="):
            suggestions.append(_clean_lynis(line.split("=", 1)[1]))
        elif line.startswith("hardening_index="):
            try:
                index = int(line.split("=", 1)[1])
            except ValueError:
                index = None
    if index is None and not warnings and not suggestions:
        return LynisResult(status="error", error="Kein Lynis-Bericht gefunden (läuft die Anmeldung als root?).")
    return LynisResult(status="ok", hardening_index=index, warnings=warnings, suggestions=suggestions)


INSTALL_COMMANDS = {
    "clamav": (
        "if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q clamav clamav-freshclam; "
        "elif command -v dnf >/dev/null; then dnf install -y clamav clamav-update; "
        "elif command -v apk >/dev/null; then apk add clamav clamav-libunrar; "
        "else echo 'Kein bekannter Paketmanager'; exit 2; fi"
    ),
    "lynis": (
        "if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q lynis; "
        "elif command -v dnf >/dev/null; then dnf install -y lynis; "
        "elif command -v apk >/dev/null; then apk add lynis; "
        "else echo 'Kein bekannter Paketmanager'; exit 2; fi"
    ),
    "fail2ban": (
        "if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban; "
        "elif command -v dnf >/dev/null; then dnf install -y fail2ban; "
        "elif command -v apk >/dev/null; then apk add fail2ban; "
        "else echo 'Kein bekannter Paketmanager'; exit 2; fi; "
        # Debian 12 hat kein /var/log/auth.log mehr; ohne systemd-Backend startet der sshd-Jail nicht.
        "if [ ! -f /etc/fail2ban/jail.local ]; then printf '[DEFAULT]\\nbackend = systemd\\n\\n[sshd]\\nenabled = true\\n' > /etc/fail2ban/jail.local; fi; "
        "systemctl enable --now fail2ban 2>/dev/null; systemctl restart fail2ban 2>/dev/null; fail2ban-client ping"
    ),
    "unattended": (
        "command -v apt-get >/dev/null || { echo 'Nur für Debian/Ubuntu (apt)'; exit 2; }; "
        "DEBIAN_FRONTEND=noninteractive apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q unattended-upgrades && "
        "printf 'APT::Periodic::Update-Package-Lists \"1\";\\nAPT::Periodic::Unattended-Upgrade \"1\";\\n' > /etc/apt/apt.conf.d/20auto-upgrades && echo ok"
    ),
    # Danach bleibt das automatische Signatur-Update eingeschaltet (`enable --now`): Ein nur gestarteter Dienst ist nach
    # dem nächsten Neustart wieder aus.
    "signatures": (
        "systemctl stop clamav-freshclam 2>/dev/null; freshclam --quiet; R=$?; "
        "systemctl enable --now clamav-freshclam 2>/dev/null; exit $R"
    ),
}
