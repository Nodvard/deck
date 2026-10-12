"""ClamAV-Scans, Quarantäne und Lynis auf den Servern: baut die Befehle und wertet die Ausgaben aus.

Ausführen, Speichern und Melden steht in `defender.py`.
"""

from __future__ import annotations

import posixpath
import re
import secrets
import shlex
from dataclasses import dataclass, field

# Die Quarantäne liegt auf dem betroffenen Server selbst.
QUARANTINE_DIR = "/var/lib/nexus-quarantine"

QUICK_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root"]
DEEP_PATHS = ["/"]
# Der Echtzeit-Wächter scannt in kurzen Abständen die zuletzt geänderten Dateien dieser Ordner; inotify bräuchte auf jedem Server
# einen Agenten.
WATCH_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root", "/opt"]
# Nur für den Wächter (Schnell- und Tiefenscan prüfen /dev/shm weiter): Die IPC-Dateien von pmxcfs/corosync auf Proxmox ändern sich
# ständig, und clamscan lädt bei jedem Lauf die ganze Datenbank.
WATCH_EXCLUDED = ["/dev/shm/qb-*"]

# clamdscan meldet eine zwischen `find` und Scan verschwundene Datei mit Code 2 und "ERROR: Can't access file ..." (ClamAV 1.0.5);
# die übrigen Dateien sind trotzdem geprüft. Erweitertes grep-Muster für die erlaubten Zeilen: genau diese Meldung und Funde.
_CLAMD_SKIP_OK = "^(ERROR|WARNING): Can.t access file | FOUND$"

# Höchstens so viele neue Dateien prüft ein Wächterlauf (schützt die Laufzeit). Den Rest zählt die Shell und meldet ihn als
# `Skipped files: N`, er wird nie still verworfen.
WATCH_MAX_FILES = 2000
# `clamscan --file-list` liest je Zeile höchstens 1023 Zeichen (`fgets(buff, 1024, ...)`); ein längerer Pfad zerfiele in Stücke, die auf
# nicht vorhandene Dateien zeigen. Solche Pfade gehen als Argumente durch (`_ODD_NAME_TEST`).
_LONG_PATH_BYTES = 1023

# Tiefenscan: virtuelle und flüchtige Dateisysteme nie durchsuchen.
_ALWAYS_EXCLUDED = ["/proc", "/sys", "/dev", "/run", QUARANTINE_DIR]

# Rückgabecode-Marke eines Scans: je Lauf eine Zufallsmarke `@@scan-rc-<16 hex>=`. Eine feste Marke ließe sich in einen Dateinamen
# schreiben (`/tmp/x@@<feste-marke>=0`), die Auswertung schnitte die Ausgabe dort ab, und der Fund verschwände. Gezogen wird die Marke
# beim Befehlsbau, bei der Auswertung wird dieselbe gebraucht (`build_*_command(..., mark=m)`, `parse_scan_output(..., m)`).
_RC_MARK_RE = re.compile(r"@@scan-rc-[0-9a-f]{16}=")
NO_ROOT = "@@noroot"
NO_ROOT_MESSAGE = (
    "Keine root-Rechte: Das Dashboard ist auf diesem Server nicht als root angemeldet und "
    "darf sudo nicht ohne Passwort nutzen."
)


def as_root(command: str, *, required: bool = True) -> str:
    """Führt `command` als root aus: direkt, wenn schon root, sonst über `sudo -n` (ohne Passwortabfrage).

    Geht beides nicht: bei `required` Abbruch mit NO_ROOT, sonst als normaler Benutzer (ein Scan prüft dann nur lesbare Dateien)."""
    inner = shlex.quote(command)
    fallback = f"sh -c {inner}" if not required else f"echo '{NO_ROOT}'; exit 126"
    return (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {inner}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {inner}; "
        f"else {fallback}; fi"
    )


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
    # Anzahl Funde: mindestens die gelesenen Fund-Zeilen, sonst die Zahl aus der ClamAV-Zusammenfassung oder 1, wenn ClamAV mit
    # Code 1 endet, obwohl keine Fund-Zeile lesbar war.
    infected: int = 0
    # Die Fund-Zeilen sind nicht verlässlich (Zeilenumbruch im Dateinamen, Zeilen, die ClamAV nie ausgibt, Zahlen, die nicht
    # zusammenpassen, Marke fehlt): `findings` kann dann gefälschte Pfade enthalten. Der Defender verschiebt in dem Fall nichts
    # automatisch (er liefe als root).
    unreliable: bool = False
    # Davon der Teil, der die Pfade der Funde betrifft (Zeilenumbruch im Namen, Pfad außerhalb der Wurzeln ...). `unreliable` ist
    # zusätzlich gesetzt, wenn der Wächter Dateien nicht geprüft hat (`skipped`).
    paths_unsure: bool = False
    # So viele neue Dateien hat der Wächter wegen der Obergrenze `WATCH_MAX_FILES` nicht geprüft.
    skipped: int = 0
    # Die Sperre des Servers war belegt (`guard_command`): Der Lauf hat gar nicht angefangen, es lief schon ein anderer.
    busy: bool = False
    # `timeout` auf dem Server hat den Lauf beendet (`guard_command`).
    timed_out: bool = False


def _q(value: str) -> str:
    return shlex.quote(value)


def _roots_label(mark: str) -> str:
    """Anfang der Zeilen mit den aufgelösten Wurzeln: `@@scan-roots-<hex>=` (dieselbe Zufallszahl wie die Marke des Laufs)."""
    return "@@scan-roots-" + mark[len("@@scan-rc-"):]


def _run_label(mark: str, word: str) -> str:
    """Eine ganze Zeile, die nur `guard_command` schreibt: `@@scan-<word>-<16 hex>` mit der Zufallszahl der Marke dieses Laufs.

    Ein Dateiname kann sie nicht vorwegnehmen (die Zahl ist erst beim Befehlsbau bekannt)."""
    return f"@@scan-{word}-{mark[len('@@scan-rc-'):-1]}"


def _resolve_roots_command(paths: list[str], mark: str) -> str:
    """Shell-Teil, der als Allererstes je Wurzel eine Zeile `<_roots_label><aufgelöster Pfad>` schreibt.

    `parse_scan_output` liest genau diese ersten Zeilen; ein Dateiname kann sie nicht vorwegnehmen."""
    # ClamAV 1.0 (`clamscan`) und `clamdscan` melden Funde mit dem aufgelösten Pfad (Symlinks ersetzt): Ist `/home` ein Link auf
    # `/data/home`, käme `/data/home/x: ... FOUND` zurück und passte nie zur Einstellung (`/home`). Darum löst die Shell jede
    # Wurzel selbst auf (`cd -P` für Ordner, sonst `readlink -f`). Ein Pfad mit Zeilenumbruch oder Wagenrücklauf ergibt eine leere
    # Zeile, ebenso einer, den beides nicht auflösen kann (fehlt nur das letzte Stück, gibt `readlink -f` den Pfad unverändert
    # zurück).
    label = _roots_label(mark)
    quoted = " ".join(_q(p) for p in paths)
    return (
        "NL=$(printf '\\nx'); NL=${NL%x}; CR=$(printf '\\r'); "
        f"for P in {quoted}; do "
        'D=$(cd -P -- "$P" 2>/dev/null && pwd -P || readlink -f -- "$P" 2>/dev/null); '
        'case "$D" in *"$NL"*|*"$CR"*) D=;; esac; '
        f"printf '%s\\n' \"{label}$D\"; done; "
    )


_ERE_SPECIAL = frozenset(".[]()*+?{}|^$\\")
# Nur ein angehängtes `/*` oder `/**` ("der Inhalt dieses Ordners") fällt weg, andere Platzhalter stehen wörtlich.
_TRAILING_WILDCARD_RE = re.compile(r"(/\*+)+/*$")
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
    folder = _TRAILING_WILDCARD_RE.sub("", folder) or "/"
    folder = "/" + posixpath.normpath(folder).lstrip("/")
    # `/` selbst zählt nie, sonst liefe gar nichts.
    return None if folder == "/" else folder


def scan_exclude_regex(folder: object) -> str | None:
    """Ausdruck für `clamscan --exclude-dir`, der genau diesen Ordner samt Inhalt trifft, oder `None`.

    Welche Einträge zählen, regelt `exclude_folder`; zu lange Pfade (`_MAX_EXCLUDE_BYTES`) werden nicht ausgenommen."""
    folder = exclude_folder(folder)
    if folder is None or len(folder.encode("utf-8", "replace")) > _MAX_EXCLUDE_BYTES:
        return None
    # ClamAV liest den Wert als regulären Ausdruck (POSIX ERE) und sucht ihn im Pfad jedes Ordners, den es betreten will; den
    # Anker `^` muss der Ausdruck selbst liefern. Darum jedes Sonderzeichen maskieren (`/srv/a+b`, `/srv/(alt)` träfen sonst
    # andere oder gar keine Ordner) und hinter dem Namen ein `/` oder das Ende verlangen: Sonst nähme `/srv/backup` auch
    # `/srv/backups-wichtig` aus, `/dev` auch `/devel`.
    escaped = "".join("\\" + ch if ch in _ERE_SPECIAL else ch for ch in folder)
    return f"^{escaped}(/|$)"


def build_scan_command(
    paths: list[str], *, max_filesize_mb: int = 50, exclude: list[str] | None = None, mark: str | None = None,
) -> str:
    """`clamscan` rekursiv, nur Funde ausgeben (`-i`), mit Zusammenfassung am Ende für die Dateizahl.

    `mark`: die Zufallsmarke dieses Laufs (`new_rc_mark()`), dieselbe geht an `parse_scan_output`; ohne Angabe wird eine gezogen."""
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


# Pfade mit Zeilenumbruch oder Wagenrücklauf (auch in einem Ordnernamen) oder mit 1023 Bytes und mehr dürfen nicht in die
# `--file-list` des Wächters: Sie kennt nur einen Pfad je Zeile mit höchstens 1023 Zeichen (ein CR am Zeilenende liest ClamAV
# nicht mit). Ein solcher Name zerfiele in Bruchstücke, ClamAV meldete "Can't access file" (Code 2), die echte Datei bliebe
# ungeprüft, und der Lauf galt als sauber. Darum trennt `find` sie ab und gibt sie stapelweise als Argumente an einen eigenen
# `clamscan` (Argumente kennen keine Zeilen und keine Längengrenze). Ein Fund darin gilt nie als eindeutig lesbar (Zeile
# `Unusual file names: N` am Ende, siehe `parse_scan_output`). Der Lauf ist selten und lädt die Datenbank jedes Mal neu, auch im
# clamd-Modus: Richtigkeit geht vor Tempo.
# - NL/CR sind Zeilenumbruch und Wagenrücklauf (`printf '\\nx'` plus Abschneiden des `x`, weil `$(...)` einen Umbruch am Ende
#   verlöre); X ist die Ausgabe, Y je Stapel "Rückgabecode Dateizahl".
# - LONG ist ein Muster aus 1023 `?` und einem `*`; `find` läuft dafür mit `LC_ALL=C`, damit ein `?` ein Byte ist und kein
#   Zeichen.
# - Die Argumente beginnen immer mit dem absoluten Startpfad von `find`, nie mit `-`.
# - Braucht GNU find: `-exec ... {} +` innerhalb von `\\( ... \\)` führt die busybox-Variante nicht aus, und schon `-size -50M`
#   kennt sie nicht ("invalid number"). Auf solchen Systemen prüft der Wächter nie etwas.
_ODD_NAME_LABEL = "Unusual file names"
_ODD_NAME_TEST = '\\( -path "*$NL*" -o -path "*$CR*" -o -path "$LONG" \\)'
_ODD_NAME_SCAN = (
    "sh -c 'o=$0; r=$1; shift; n=$#; clamscan -i --stdout --no-summary \"$@\" >> \"$o\" 2>&1; "
    "echo \"$? $n\" >> \"$r\"' \"$X\" \"$Y\" {} +"
)
# Rückgabecodes der Sonderläufe in den Gesamtcode (R) einrechnen: 127 (nicht installiert) vor 1 (Fund) vor allem anderen; ein Fehler
# (2, Absturz ...) nur, wenn bisher alles sauber war oder nur Code 2 vorlag.
_ODD_NAME_MERGE = (
    'T=0; while read -r r n; do T=$((T + ${n:-0})); case "$r" in 0) ;; '
    '1) [ "$R" -eq 127 ] || R=1;; '
    '*) [ "$R" -eq 0 ] || [ "$R" -eq 2 ] && R=$r;; esac; done < "$Y"; '
    f'[ "$T" -eq 0 ] || echo "{_ODD_NAME_LABEL}: $T"; '
)
# Die Dateien über der Obergrenze: `awk` schreibt die ersten `WATCH_MAX_FILES` Pfade in die Liste und zählt den Rest in die Datei K (nur
# die Zahl). `awk` liest alles, damit `find` nie per SIGPIPE abbricht, bevor es die Dateien mit Sondernamen an den Sonderlauf übergibt
# (`head` plus `wc -l` ginge nicht: `head` liest von einer Pipe blockweise und wirft dabei Zeilen weg, der Rest wäre zu klein gezählt).
# Vor der Marke schreibt die Shell dann, nur bei einem Rest, `Skipped files: N`: So steht die Zeile hinter allen Dateinamen, und der
# letzte Treffer gilt.
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

    Die Ordner kommen aus „Ausgenommene Ordner“ (`exclude_paths`), wie beim Scan; welche zählen, regelt `exclude_folder`."""
    # `find` nennt Pfade mit einfachen Schrägstrichen (`/a//b` oder `/a/./b` griffe sonst nie), darum die Vereinfachung in
    # `exclude_folder`. Bei einer Wurzel, die selbst eine Verknüpfung ist, vergleicht `find` mit ihrem Namen, nicht mit dem Ziel.
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

    `exclude`: find-Muster (Standard `WATCH_EXCLUDED`), `skip_folders`: ausgenommene Ordner, `use_clamd`: über clamd statt clamscan."""
    mark = _checked_mark(mark)
    finds = " ".join(_q(p) for p in paths)
    skip = " ".join(f"-not -path {_q(p)}" for p in (WATCH_EXCLUDED if exclude is None else exclude))
    clamscan = 'clamscan -i --stdout --file-list="$L" 2>&1; R=$?'
    if use_clamd:
        # Antwortet ein clamd (`clamdscan --ping 1`), prüft der Wächter über ihn (`--fdpass`, die Datenbank ist dort schon
        # geladen), sonst mit clamscan. Die Ausgabe hat das Format für `parse_scan_output`; `Scanned files: N` kommt aus der
        # Dateiliste, weil clamdscan ohne Zusammenfassung läuft. Nur ein sauberer clamdscan-Lauf zählt: Code 0, oder 1/2 mit
        # nichts als `_CLAMD_SKIP_OK` in der Ausgabe (eine Datei, die zwischen `find` und Scan verschwand, ist in /tmp und
        # /dev/shm Alltag, und ein Neustart von clamscan, rund 20 s und 1 GB, wäre genau das, was die Einstellung sparen soll).
        # clamdscan meldet bei einem Fund Code 1, auch wenn danach Verbindungsfehler kamen. Alles andere (clamd zwischen Ping und
        # Scan weg, Verbindungsfehler, auch eine leere Ausgabe) wird verworfen und mit clamscan wiederholt: Sonst zählten bei Code
        # 1 plus Verbindungsfehler die Dateien nach dem Fund ungeprüft als geprüft, und ein misslungener Scan darf nie als
        # "sauber" durchgehen.
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
        # Die Dateiliste darf nicht selbst ein Treffer sein: `mktemp` legt sie ohne TMPDIR (über SSH/sudo nie gesetzt) in /tmp an,
        # und /tmp wird überwacht, dann liefe clamscan (lädt jedes Mal die ganze Datenbank) bei jedem Lauf. Darum nach /run (als
        # root beschreibbar, nie überwacht) und die Liste zusätzlich ausschließen (mktemp-Namen enthalten keine Glob-Zeichen).
        # Vier Dateien: die Liste, die Ausgabe des Sonderlaufs (X), dessen Rückgabecodes (Y) und die Zahl der Dateien über der
        # Obergrenze (K); keine davon darf `find` selbst finden. Dateien mit Sondernamen: siehe `_ODD_NAME_SCAN`. Als Allererstes
        # die aufgelösten Wurzeln (`_resolve_roots_command`), noch vor jeder Ausgabe von Dateinamen.
        _resolve_roots_command(paths, mark)
        # Beendet das Zeitlimit auf dem Server den Lauf (`guard_command`, erst TERM), bleiben die vier Dateien nicht liegen.
        + "trap 'rm -f \"$L\" \"$X\" \"$Y\" \"$K\" 2>/dev/null; exit 143' TERM; "
        "L=$(mktemp -p /run 2>/dev/null || mktemp); X=$(mktemp -p /run 2>/dev/null || mktemp); "
        "Y=$(mktemp -p /run 2>/dev/null || mktemp); K=$(mktemp -p /run 2>/dev/null || mktemp); "
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
# Jeder clamscan lädt die ganze Datenbank (rund 1 GB). Laufen Wächter und Schnellscan gleichzeitig auf einem Server, kann er hängen
# bleiben, und beide Läufe enden mit einer Zeitüberschreitung. Darum nimmt jeder Lauf zuerst eine Sperre auf dem Server (`flock` aus
# util-linux): der Wächter nur, wenn sie frei ist (sonst setzt er aus), ein Scan wartet eine Weile.
#
# Wo die Sperrdatei liegt: `flock` öffnet sie mit `O_CREAT` und folgt dabei Verknüpfungen, und eine Sperre lässt sich auch über eine nur
# lesbare Datei halten. In einem Ordner, den andere beschreiben oder lesen können (`/tmp`, `/run/lock` mit Sticky-Bit), könnte jemand
# die Datei vorher anlegen, eine Verknüpfung legen, die root dann öffnet, oder die Sperre festhalten und so den Wächter abschalten.
# Darum liegt sie in einem eigenen Ordner mit Rechten 700, der dem ausführenden Benutzer gehören muss: als root `/run/nodvard-shield`
# (in `/run` darf nur root anlegen, nach einem Neustart ist er weg), ohne root im Heimatordner. Lässt sich der Ordner nicht anlegen,
# gehört er jemand anderem oder hat er andere Rechte, läuft der Scan ohne Sperre.
# Ohne root trägt der Dateiname die Kennung des Rechners (`scan-<Rechnername>-<machine-id>.lock`): Liegt der Heimatordner auf einem
# Netzlaufwerk (NFS), ist er auf allen Servern derselbe, und Linux reicht `flock` dort an den NFS-Server weiter. Mit einem festen Namen
# teilten sich alle Server eine Sperre, und der Wächter setzte überall aus, sobald irgendwo ein Scan lief. Beide Teile, weil geklonte
# VMs oft dieselbe machine-id und frische Installationen oft denselben Rechnernamen haben. Fehlt beides, bleibt `scan--.lock`.
LOCK_DIR_ROOT = "/run/nodvard-shield"
LOCK_DIR_USER = ".nodvard-shield"
LOCK_FILE = "scan.lock"
_LOCK_FILE_USER = (
    # Jedes Werkzeug mit eigenem `2>/dev/null`: Fehlt eins (sehr kleine Systeme), meldet die Shell sonst "not found", und das
    # landete in der Ausgabe des Scans. Der Name ist dann eben kürzer (`scan--.lock`), die Sperre gilt trotzdem.
    "scan-$(uname -n 2>/dev/null | tr -cd 'A-Za-z0-9._-' 2>/dev/null | cut -c1-64 2>/dev/null)"
    "-$(cat /etc/machine-id 2>/dev/null | tr -cd 0-9a-f 2>/dev/null | cut -c1-32 2>/dev/null).lock"
)
# Zeitlimit auf dem Server: Bricht die Verbindung ab oder läuft das Dashboard-Limit ab, läuft der Befehl auf dem Server sonst
# weiter (und hält die Sperre). `timeout` beendet ihn vorher: TERM nach Limit minus 45 Sekunden, KILL 15 Sekunden später, also 30
# Sekunden vor dem Limit des Dashboards. So kommt die Meldung der Zeitüberschreitung noch an.
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

    Die Ausgabe bleibt gleich, dazu kommt höchstens `@@scan-busy-<hex>` (Sperre belegt) oder `@@scan-timeout-<hex>` (Zeitlimit)."""
    mark = _checked_mark(mark)
    limit = server_timeout_s(limit_s)
    # `wait_s=None` (Wächter) gibt sofort auf (`flock -n`), sonst wartet der Lauf bis zu `wait_s` Sekunden (`flock -w`); `command`
    # läuft dann gar nicht. Das Zeitlimit (Code 124, oder 137 nach KILL) lässt die Marke von `command` fehlen. Ohne `flock` aus
    # util-linux (BusyBox, NAS) läuft `command` ohne Sperre, ohne `timeout` aus den coreutils (oder eines, das `-k` nicht kennt)
    # ohne Zeitlimit; keines von beiden lässt den Lauf scheitern. Endet `flock` anders als mit "belegt" (Code 1), z. B. auf einem
    # Dateisystem ohne Sperren, läuft der Scan ebenfalls ohne Sperre. Die Wartezeit zählt zum Zeitlimit.
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


_FOUND_RE = re.compile(r"^(?P<path>/.*): (?P<sig>.+) FOUND$")
_SCANNED_RE = re.compile(r"^Scanned files: ([0-9]+)")
_INFECTED_RE = re.compile(r"^Infected files: ([0-9]+)")
# Zeile, die der Wächter-Befehl selbst am Ende schreibt, wenn er Dateien mit Zeilenumbruch im Namen separat geprüft hat
# (`_ODD_NAME_MERGE`): Kein Dateiname steht dahinter, nur die Zahl aus der Shell.
_ODD_NAMES_RE = re.compile(r"^" + re.escape(_ODD_NAME_LABEL) + r": ([0-9]+)$")
# Zeile des Befehls mit dem Rest über der Obergrenze des Wächters (`_SKIPPED_REPORT`): ebenfalls nur die Zahl aus der Shell.
_SKIPPED_RE = re.compile(r"^" + re.escape(_SKIPPED_LABEL) + r": ([0-9]+)$")
# Meldungen über eine Datei, die ClamAV nicht lesen konnte. Die Formate stammen aus dem Quelltext (clamscan/manager.c,
# clamdscan/proto.c, ClamAV 0.103 bis 1.4); der Pfad steht roh darin, gebraucht wird er nur, um ihn mit den Wurzeln zu vergleichen:
# - clamdscan: "ERROR: Can't access file <Pfad>" (0.103 und 1.x, bei --file-list und bei Argumenten);
# - clamscan, Eintrag der --file-list, den es nicht gibt (der Fall, den ein zerrissener Name auslöst): zuerst "<Pfad>: <Grund>"
#   (perror auf stderr, z. B. "No such file or directory"), dann "WARNING: <Pfad>: Can't access file", Rückgabecode 2;
# - clamscan, Datei beim Scan nicht zu öffnen: "WARNING: Can't open file <Pfad>: <Grund>" (Code 2);
# - clamd (über clamdscan): "<Pfad>: <Grund>. ERROR".
_ACCESS_ERROR_RE = re.compile(r"^(?:ERROR|WARNING): Can.t (?:access|open) (?:file|directory):? (?P<path>.+)$")
_ACCESS_WARNING_TAIL_RE = re.compile(r"^WARNING: (?P<path>.+): Can.t access file$")
_ACCESS_ERROR_TAIL_RE = re.compile(r"^(?P<path>/.*): [^:]*\. ERROR$")


def _is_access_message(line: str) -> bool:
    """Eine Meldung über eine Datei, die ClamAV nicht lesen konnte (kein Hinweis auf einen kaputten Lauf)."""
    return _ACCESS_ERROR_RE.match(line) is not None or _ACCESS_WARNING_TAIL_RE.match(line) is not None


# Meldungen über eine Datei, die es beim Scan nicht mehr gab oder die keine normale Datei mehr war (mit clamscan 1.0.5 und 1.5.4
# nachgeprüft; Muster wie oben): "WARNING: <Pfad>: Can't access file" nach "<Pfad>: No such file or directory"
# (`_ACCESS_WARNING_TAIL_RE`), "WARNING: Can't open file <Pfad>: No such file or directory" (verschwand zwischen Prüfung und
# Öffnen, zählt bei "Total errors" mit) und "WARNING: <Pfad>: Not supported file type" (inzwischen z. B. eine Pipe). "WARNING:
# <Pfad>: Can't access file" allein sagt nichts über den Grund: Den schreibt clamscan in der Zeile davor (`perror`), und nur "No
# such file or directory" oder "Not a directory" heißt "verschwunden". "Input/output error" (Platte, Netzlaufwerk), "Permission
# denied" oder eine fehlende Zeile davor heißen: Die Datei ist noch da und wurde nicht geprüft. Eine Datei ohne Leserecht meldet
# clamscan dagegen mit "Can't open file <Pfad>: Permission denied", einen nicht lesbaren Ordner mit `-i` gar nicht: dann steht nur
# "Total errors: N" in der Zusammenfassung.
_GONE_REASONS = ("No such file or directory", "Not a directory")
_GONE_OPEN_RE = re.compile(r"^WARNING: Can.t open file (?P<path>.+): (?:No such file or directory|Not a directory)$")
_UNSUPPORTED_RE = re.compile(r"^WARNING: (?P<path>.+): Not supported file type$")
_TOTAL_ERRORS_RE = re.compile(r"^Total errors: ([0-9]+)$")
# Die Zusammenfassung von ClamAV (Kopfzeile und alle Zeilen darunter, clamscan 0.103 bis 1.5): nie der Grund eines Fehlers. Bei clamscan
# ist "End Date:" immer die letzte Zeile und würde sonst als Grund gewählt.
_SUMMARY_LINE_RE = re.compile(
    r"^(?:-+ SCAN SUMMARY -+|(?:Known viruses|Engine version|Scanned directories|Scanned files|Infected files|Total errors"
    r"|Not removed|Not moved|Not copied|Data scanned|Data read|Time|Start Date|End Date):.*)$"
)


def _has_text(line: str) -> bool:
    """Steht hinter dem ersten Doppelpunkt ein Wort (nicht nur Sternchen wie im Rahmen der Warnung zur alten Datenbank)?"""
    return re.search(r"[A-Za-z]", line.split(":", 1)[-1]) is not None


def _failure_reason(
    lines: list[str], rc: int, *, unread: int = 0, gone_lines: frozenset[str] | set[str] = frozenset(),
    perror: dict[str, list[str]] | None = None,
) -> str:
    """Der Grund für einen gescheiterten Lauf, aus den (nicht leeren) Zeilen der Ausgabe."""
    # Rangfolge:
    # 1. die erste Fehlerzeile mit Text (`ERROR:`, `LibClamAV Error`);
    # 2. die erste Warnung von clamscan (`WARNING:`), die nicht nur eine verschwundene Datei meldet (`gone_lines`). Bei
    #    "WARNING: <Pfad>: Can't access file" steht der Grund in der Zeile davor (`perror`, je Pfad): dann diese Zeile, etwa
    #    "<Pfad>: Input/output error";
    # 3. Fehler laut Zusammenfassung, die keine Meldung erklärt (`unread`, aus "Total errors"): Einen nicht lesbaren Ordner meldet
    #    clamscan mit `-i` sonst gar nicht;
    # 4. die erste Warnung über eine verschwundene Datei;
    # 5. die erste Warnung der Bibliothek mit Text (`LibClamAV Warning`), etwa "Datenbank älter als 7 Tage". Sie steht beim Laden der
    #    Datenbank vor allen anderen Zeilen und ist fast nie die Ursache, darum erst hier;
    # 6. die letzte Zeile, die weder zur Zusammenfassung von ClamAV noch zu den eigenen Zeilen des Befehls (`@@...`, Sonderdateien,
    #    Obergrenze) gehört und keine Warnung ohne Text (nur Sternchen) ist;
    # 7. ein Satz mit dem Code.
    perror = perror or {}
    for line in lines:
        if line.startswith(("ERROR:", "LibClamAV Error")) and _has_text(line):
            return line
    for line in lines:
        if line.startswith("WARNING:") and line not in gone_lines and _has_text(line):
            tail = _ACCESS_WARNING_TAIL_RE.match(line)
            reasons = [r for r in perror.get(tail.group("path"), []) if r not in _GONE_REASONS] if tail else []
            return f"{tail.group('path')}: {reasons[0]}" if tail and reasons else line
    if unread > 0:
        return (f"ClamAV endete mit Code {rc}. Laut Zusammenfassung konnte es {unread} Datei(en) oder Ordner nicht lesen, "
                "oft fehlen dafür die Rechte.")
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
    """Text, wenn `timeout` auf dem Server den Lauf beendet hat; `limit_s` ist das Zeitlimit des Dashboards für diese Art."""
    when = f" nach etwa {duration_text(limit_s)}" if limit_s else ""
    return f"Zeitüberschreitung: Der Scan hat zu lange gebraucht und wurde{when} auf dem Server beendet."


BUSY_MESSAGE = (
    "Scan nicht gestartet: Auf dem Server lief noch ein anderer Scan und wurde nicht rechtzeitig fertig. "
    "Versuch es später noch einmal."
)
CONNECTION_LOST_MESSAGE = "Scan fehlgeschlagen: Die Verbindung zum Server ist abgebrochen, bevor der Scan fertig war."


def _has_label(lines: list[str], mark: str | None, word: str) -> bool:
    """Steht die Zeile `@@scan-<word>-<hex>` (`guard_command`) dieses Laufs in der Ausgabe? Ohne `mark` zählt jede Zufallszahl."""
    if mark is not None:
        return _run_label(_checked_mark(mark), word) in lines
    return any(re.fullmatch(rf"@@scan-{word}-[0-9a-f]{{16}}", line) for line in lines)


# Der Grund hinter dem Pfad bei `perror`: ein Satz aus Buchstaben ("No such file or directory", "Permission denied").
_ERRNO_REASON_RE = re.compile(r"^[A-Z][a-z][A-Za-z ,'-]*$")
# Die Meldung der Shell selbst ("sh: 1: clamscan: not found", "bash: line 1: clamscan: command not found").
_NOT_INSTALLED_RE = re.compile(r"^(?:/(?:usr/)?bin/)?(?:ba|da|a)?sh: (?:(?:line )?[0-9]+: )?clamscan: (?:command )?not found$")
NOT_INSTALLED_MESSAGE = "ClamAV ist auf diesem Server nicht installiert."
# Der feste Anfang des Hinweises zur Obergrenze: Daran erkennt der Defender später eine Scan-Zeile, die nur wegen der Obergrenze
# unvollständig war (die Zahl dahinter wechselt von Lauf zu Lauf).
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
        # Nie `/` als aufgelöste Wurzel, außer die eingetragene ist selbst `/`: Ein Link auf `/` weitete die Prüfung auf alles aus.
        if norm is not None and (norm != "/" or _absolute(root) == "/"):
            resolved.append(norm)
    return resolved, "\n".join(lines[len(roots):])


def parse_scan_output(
    output: str, mark: str | None = None, roots: list[str] | None = None, limit_s: int | None = None,
) -> ScanResult:
    """Wertet die Ausgabe von `build_scan_command` / `build_watch_command` aus.

    Sie enthält Dateinamen vom gescannten Server, und ClamAV schreibt sie unverändert hin: Ein Name darf die Auswertung nie steuern."""
    # ClamAV 1.0.5 gibt Zeilenumbruch, Wagenrücklauf, Seitenvorschub und ESC im Namen roh aus (geprüft). Es zählt der letzte
    # Treffer der Marke am Zeilenanfang: Die echte Marke steht als Allerletztes in der Ausgabe, ein Dateiname mit Marke darin
    # (oder mit Zeilenumbruch davor) steht immer davor und kann nichts verstecken. Alles nach der Marke fliegt raus, alles davor
    # wird ausgewertet. Ohne `mark` gilt jede Marke im Format `@@scan-rc-<16 hex>=`, eine feste Marke nie. Fehlt die Marke ganz,
    # wurde der Lauf nicht ordentlich beendet: Fehler, nie "sauber".
    pattern = _RC_MARK_RE.pattern if mark is None else re.escape(_checked_mark(mark))
    matches = list(re.finditer(r"^" + pattern + r"([0-9]+)[ \t\r]*$", output, re.MULTILINE))
    rc_match = matches[-1] if matches else None
    rc = int(rc_match.group(1)) if rc_match else None
    body = output[: rc_match.start()] if rc_match else output

    if rc == 127:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)

    check_roots: list[str] | None = None
    # `roots`: die Ordner und Pfade, die der Befehl gescannt hat (ohne Angabe unbekannt, dann keine Prüfung). Sind die Wurzeln
    # Symlinks (oder liegt einer im Pfad), melden ClamAV 1.0 und `clamdscan` Funde mit dem aufgelösten Pfad; der Befehl schreibt
    # ihn als erste Zeilen (`_resolve_roots_command`), und für die Prüfung gelten die eingetragene und die aufgelöste Wurzel.
    # Aufgelöst wird nur die Wurzel selbst: Ein Link innerhalb der Wurzel, der nach `/etc` zeigt, macht einen Fund in `/etc` nicht
    # sicher. Grenze: Wer den Elternordner einer Wurzel beschreiben darf (z. B. `/home/<name>` für die Wurzel
    # `/home/<name>/Downloads`), kann sie durch einen Link auf einen anderen Ordner ersetzen (nur nicht `/`); Funde dort gelten
    # dann als sicher. Das sind aber immer Pfade, die ClamAV selbst aufgelöst und geprüft hat, keine Zeilen aus einem Dateinamen.
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
    # Zeilen nur am Zeilenvorschub trennen, nicht mit `str.splitlines()`: Das trennt auch bei Wagenrücklauf, Seitenvorschub,
    # U+0085, U+2028 und mehr, und eine Fund-Zeile mit so einem Zeichen im Namen fiele auseinander (der Fund würde unsichtbar, der
    # Lauf "sauber").
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        nonblank.append(line)
        is_found_line = line.endswith(" FOUND")
        found_lines += int(is_found_line)
        # Die Regex nur auf Zeilen, die wie ein Fund enden: Auf langen Bruchstücken mit vielen ": " (Dateiname mit Zeilenumbruch) würde
        # sie sonst quadratisch zurücksetzen und die Auswertung, damit das ganze Dashboard, sekundenlang blockieren.
        m = _FOUND_RE.match(line) if is_found_line else None
        if m:
            findings.append((m.group("path"), m.group("sig")))
        elif line.startswith("/"):
            # Mit `-i` schreibt ClamAV einen Pfad nur in Fund-Zeilen. Der Anfang eines Fundes mit Zeilenumbruch im Namen ("/tmp/q")
            # beginnt immer mit dem Pfad des gescannten Ordners.
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
    # "Can't access file" heißt nur dann "verschwunden", wenn clamscan es für genau diesen Pfad davor so begründet hat (und nur so).
    # Jeder andere oder fehlende Grund: Die Datei kann noch da sein und wurde nicht geprüft.
    for line, path in tails:
        if perror.get(path) and all(reason in _GONE_REASONS for reason in perror[path]):
            gone_paths.append(path)
            gone_lines.add(line)
        else:
            blocking += 1

    # Endet ClamAV mit Code 1, ist es mindestens ein Fund, auch wenn keine Zeile lesbar war. "Nicht installiert" gilt nur als
    # ganze Zeile der Shell oder bei Code 127, nie als Teilstück eines Dateinamens.
    infected_count = max(len(findings), summary_infected or 0, 1 if rc == 1 else 0)
    if shell_not_found and not infected_count:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)
    # Die Zusammenfassung von ClamAV selbst (ohne die Sonderdateien): Nur sie zeigt, dass der Lauf bis zum Ende kam. Die Zahl der
    # Sonderdateien (`Unusual file names: N`) stammt von der Shell; ihre Fund-Zeilen stehen roh in der Ausgabe und lassen sich
    # nicht sicher lesen.
    summary_files = files
    if odd_names:
        files = (files or 0) + odd_names

    # Pfade, die nicht zu den gescannten Wurzeln passen, stammen aus einem Dateinamen (oder sind ein Bruchstück davon): Fund und
    # Lauf sind `unreliable`, und ein Lauf mit Code 2 gilt dann nicht als sauber. Das ist die zweite Absicherung hinter dem
    # Abgleich mit der Zusammenfassung; im clamd-Modus gibt es keine Zusammenfassung, dort ist es die einzige.
    foreign = check_roots is not None and (
        any(not _under_roots(path, check_roots) for path, _sig in findings)
        or any(not _under_roots(path, check_roots) for path in access_errors)
    )
    # Nicht alle neuen Dateien geprüft (Obergrenze des Wächters): nie "sauber", nie automatisch verschieben.
    incomplete = skipped_message(skipped) if skipped else None
    # Code 2 ohne geprüfte Datei ist nur dann ein sauberer Lauf, wenn ClamAV für jede Datei meldet, dass sie verschwunden oder
    # keine normale Datei mehr ist (beim Wächter Alltag: die Datei in /tmp ist zwischen `find` und Scan weg), die Zahl "Total
    # errors" dazu passt und keine Meldung eine eingetragene Wurzel selbst betrifft. "WARNING: <Pfad>: Can't access file" zählt
    # dabei nur mit dem Grund "No such file or directory" (oder "Not a directory") in der Zeile davor für denselben Pfad. Eine
    # Datei ohne Leserecht oder mit Ein-/Ausgabefehler, ein nicht lesbarer Ordner (mit `-i` nur als "Total errors: N" sichtbar),
    # eine fehlende Wurzel oder eine nicht ladbare Datenbank (auch dann schreibt clamscan eine Zusammenfassung mit 0 Dateien)
    # bleiben Fehler. Ohne `roots` lässt sich eine fehlende Wurzel nicht erkennen.
    root_paths = {_absolute(root) for root in check_roots or []}
    nothing_left = (
        rc == 2 and summary_files == 0 and summary_infected is not None and not odd_names and check_roots is not None
        and bool(gone_paths) and not blocking and total_errors <= gone_open
        and not any(_absolute(path) in root_paths for path in gone_paths)
    )

    if infected_count:
        # Mit Zeilenumbruch im Dateinamen zerfällt ein Fund in mehrere Zeilen, und die letzte kann wie ein Fund für einen ganz
        # anderen Pfad aussehen (`/etc/passwd: Sig FOUND`). Solche Läufe sind `unreliable`: eine Zeile mit Pfad am Anfang, die
        # kein Fund ist, oder eine Zahl von Fund-Zeilen, die nicht zu `Infected files:` passt (die Zusammenfassung steht nach
        # allen Namen, ihr letzter Treffer ist echt). Ein Fund bleibt trotzdem ein Fund. Ebenso bei Sonderdateien und bei Pfaden
        # außerhalb der Wurzeln.
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
        # Code 2 heißt hier: ClamAV konnte einzelne Dateien nicht lesen. `files` zählt aber auch die Sonderdateien mit (die Zahl
        # stammt von der Shell, ihre Läufe haben keine Zusammenfassung) und beweist deshalb nichts: Eine kaputte Datenbank plus
        # eine Datei mit Zeilenumbruch im Namen sähe sonst aus wie ein Lauf über eine Datei. Darum zählt nur die Zusammenfassung
        # von ClamAV selbst, und mit Sonderdateien (Code 2 kann dann aus deren Lauf stammen) dürfen außerdem nur Meldungen über
        # nicht lesbare Dateien vorliegen.
        other_errors = [e for e in errors if not _is_access_message(e)]
        if odd_names and other_errors:
            return ScanResult(status="error", files_scanned=files, errors=errors,
                              error=f"Scan fehlgeschlagen: {other_errors[0]}")
        if foreign:
            # "Kann Datei nicht lesen" für einen Pfad, den dieser Lauf gar nicht gescannt hat: das Bruchstück eines Dateinamens mit
            # Zeilenumbruch. Dahinter steckt eine Datei, die nie geprüft wurde: nicht "sauber".
            return ScanResult(status="error", files_scanned=files, errors=errors, error=AMBIGUOUS_OUTPUT_MESSAGE)
        # Einzelne Dateien/Ordner nicht lesbar (meist fehlende Rechte, oder zwischen `find` und Scan verschwunden), der Rest ist sauber.
        # Die Meldungen bleiben in `errors` sichtbar.
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
    # Die Abfrage läuft ohne root. Debian und Raspberry Pi OS legen Lynis nach /usr/sbin, das ein normaler Benutzer (auch per SSH) nicht
    # im Suchpfad hat: Ohne diese Zeile gälte Lynis nach der Installation weiter als "nicht installiert".
    "PATH=\"$PATH:/usr/local/sbin:/usr/sbin:/sbin\"; "
    "echo @@clam; (clamscan --version 2>/dev/null || echo none); "
    # Kein `|| echo unknown`: Bei einem gestoppten Dienst gibt systemctl "inactive" mit rc 3 aus, dahinter stünde sonst noch "unknown",
    # und "aus" würde nie erkannt.
    "echo @@fresh; (systemctl is-active clamav-freshclam 2>/dev/null; true); "
    # `lynis --version` endet sofort; `lynis show version` legt erst den Bericht neu an (und räumt die PID-Datei eines laufenden
    # Audits weg).
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


def quarantine_command(path: str, quarantine_name: str) -> str:
    """Verschiebt die Datei in den Tresor, merkt sich die Rechte (Ausgabe) und macht sie unlesbar und unausführbar.

    Bricht ab, wenn die Datei nicht (mehr) existiert, ein Link im Weg ist oder die Datei weitere Hardlinks hat."""
    # Symbolischen Links wird nie gefolgt: `[ -f ]` und `chmod` folgen ihnen, `mv` verschiebt nur den Link; als root träfe `chmod
    # 000` sonst das Linkziel (z. B. /etc/shadow). Ebenso darf kein Ordner im Pfad inzwischen ein Link sein (sonst wanderte die
    # echte /etc/shadow in den Tresor). Darum erst in den Ordner wechseln (`cd -P`, danach ändert ein Tausch des Ordners nichts
    # mehr), dort nur mit dem Dateinamen arbeiten und nach dem `mv` noch einmal prüfen (Tausch zwischen Prüfung und `mv`; im
    # Tresor, nur für root, kann niemand mehr tauschen). Hardlinks: `chmod` ändert den Inode, und ein Inode kann mehrere Namen
    # haben. Hängt ein Angreifer im Rennen zwischen Prüfung und `mv` an den Namen der Datei einen Hardlink auf eine Systemdatei
    # (/etc/shadow, gleiches Dateisystem), landet beim `mv` nur ein weiterer Name dieser Datei im Tresor, und `chmod 000` machte
    # die Systemdatei unlesbar. Darum muss die Datei im Tresor genau einen Namen haben, sonst gibt es kein `chmod`: Der
    # Tresor-Name wird wieder entfernt (`rm` löst nur diesen Namen, die Datei bleibt unter ihren anderen) und der Befehl endet mit
    # Fehler. Die Prüfung vor dem `mv` fängt nur den harmlosen Fall ab (es wird nichts angefasst); entscheidend ist die danach,
    # denn im Tresor kann nur root noch etwas ändern. Eine Datei mit echten Hardlinks kommt deshalb nie in die Quarantäne (sie
    # bekommt eine Meldung statt eines stillen Fehlschlags).
    parent, _, name = path.rpartition("/")
    if not path.startswith("/") or name in ("", ".", ".."):
        raise ValueError("Ungültiger Dateipfad.")
    parent = parent or "/"
    here = _q("./" + name)
    target = _q(f"{QUARANTINE_DIR}/{quarantine_name}")
    return (
        f"set -e; cd -P {_q(parent)} 2>/dev/null || {{ echo 'Datei nicht mehr vorhanden'; exit 3; }}; "
        f"[ \"$(pwd -P)\" = {_q(parent)} ] || {{ echo {_q(PATH_LINK_REFUSED)}; exit 5; }}; "
        f"[ -L {here} ] && {{ echo {_q(SYMLINK_REFUSED)}; exit 5; }}; "
        f"[ -f {here} ] || {{ echo 'Datei nicht mehr vorhanden'; exit 3; }}; "
        f"[ \"$(stat -c %h {here})\" = 1 ] || {{ echo {_q(HARDLINK_REFUSED)}; exit 5; }}; "
        f"mkdir -p {QUARANTINE_DIR}; chmod 700 {QUARANTINE_DIR}; "
        f"M=$(stat -c %a {here}); mv -f {here} {target}; "
        f"if [ -L {target} ]; then rm -f {target}; echo {_q(SYMLINK_SWAPPED)}; exit 5; fi; "
        f"if [ \"$(stat -c %h {target})\" != 1 ]; then rm -f {target}; echo {_q(HARDLINK_SWAPPED)}; exit 5; fi; "
        f"chmod 000 {target}; echo \"@@mode=$M\""
    )


RESTORE_FOLDER_MISSING = "Der Ursprungsordner ist nicht mehr da oder nicht erreichbar – bitte zuerst auf dem Server wieder anlegen."
RESTORE_TARGET_TAKEN = "Am Ursprungsort liegt inzwischen eine andere Datei oder Verknüpfung – nichts überschrieben, die Datei bleibt in der Quarantäne."
RESTORE_NOT_MOVED = "Die Datei ließ sich nicht zurückverschieben – sie bleibt in der Quarantäne."
RESTORE_STAGE_FAILED = ("Im Ursprungsordner ließ sich kein geschützter Zwischenordner anlegen (zum Beispiel auf einem "
                        "Laufwerk ohne Dateirechte oder in einem schreibgeschützten Ordner) oder er wurde ausgetauscht – "
                        "aus Sicherheitsgründen nicht wiederhergestellt.")
RESTORE_STUCK = "Die Datei ließ sich weder zurücklegen noch in die Quarantäne zurückholen. Sie liegt nur für root lesbar hier:"
# Zwischenordner im Ursprungsordner (`mktemp -d`: neu, Rechte 700, gehört root; in einem Ordner mit gesetztem Gruppen-Bit erbt er es und
# zeigt 2700, das ist derselbe Ordner, nur für root).
RESTORE_STAGE_TEMPLATE = "./.nodvard-wiederherstellen.XXXXXX"


def restore_command(quarantine_path: str, original_path: str, mode: str | None) -> str:
    """Zurück an den Ursprungsort, wie beim Verschieben nie über einen Ordner, der inzwischen eine Verknüpfung ist.

    Als root, mit Besitzer und Rechten der Datei: Eine untergeschobene Verknüpfung legte sie sonst z. B. in /etc/profile.d ab."""
    # Der Ursprungsordner gehört oft einem normalen Benutzer. Er kann dort jederzeit `./name` anlegen, auch zwischen jeder Prüfung
    # und dem `mv`. Liegt die Quarantäne auf einem anderen Dateisystem, ist `mv` ein Kopieren, und wie sicher das ist, hängt vom
    # Programm ab: GNU legt mit O_EXCL an und setzt Rechte über den offenen Dateideskriptor, BusyBox setzt Rechte und Zeiten über
    # den Pfad, uutils vor 0.10 (Ubuntu 26.04 ohne Updates) folgt beim Anlegen einer Verknüpfung. Darum zwei Schritte:
    # 1. Die Datei kommt in einen frischen Zwischenordner im Ursprungsordner (`mktemp -d`, gehört root, Rechte 700). Nach `cd -P`
    #    hinein wird geprüft, dass es wirklich dieser Ordner ist (Pfad, Besitzer, Rechte); darin kann niemand außer root etwas
    #    anlegen, das Kopieren ist dort mit jedem `mv` sicher. Hat der Ursprungsordner das Gruppen-Bit gesetzt (setgid, z. B.
    #    Gruppen-Freigaben, /usr/local/*, /var/mail), erbt der Zwischenordner es, und `stat` meldet 2700 statt 700; auch das gilt
    #    als in Ordnung (das Bit gibt niemandem Zugriff). Scheitert eine der Prüfungen, wird der leere Zwischenordner wieder
    #    entfernt (`cd` zurück in den Ursprungsordner, `rmdir`; ein vom Benutzer untergeschobener Ordner mit diesem Namen
    #    verschwindet, falls leer, eine untergeschobene Verknüpfung bleibt stehen, `rmdir` löst keine auf), damit nicht bei jedem
    #    Versuch ein neuer liegen bleibt.
    # 2. Von dort ein Umbenennen im selben Ordner, also auf demselben Dateisystem (`rename`, nie Kopieren): `mv -T -n ./name
    #    ../name`. `rename` folgt am letzten Pfadteil keiner Verknüpfung, `-T` behandelt das Ziel nie als Ordner, `-n`
    #    überschreibt nichts. `..` ist der Ursprungsordner: Einen root-Ordner ohne Schreibrecht kann der Benutzer nicht in einen
    #    anderen Ordner verschieben.
    # Ältere mv und BusyBox melden bei `-n` Erfolg, auch wenn sie nichts verschoben haben, und manche kennen die Optionen nicht:
    # Entscheidend ist deshalb immer, ob die Quelle danach weg ist. Klappt Schritt 2 nicht, wandert die Datei zurück in die
    # Quarantäne (wieder `000`). Die Rechte werden vorher an der Datei in der Quarantäne gesetzt (dort kann niemand tauschen);
    # `mv` behält sie. Der Ursprungsordner wird nie angelegt (`mkdir -p` folgte Verknüpfungen in Zwischenordnern): Fehlt er,
    # bricht der Befehl ab.
    safe_mode = mode if mode and re.fullmatch(r"[0-7]{3,4}", mode) else "644"
    parent, _, name = original_path.rpartition("/")
    if not original_path.startswith("/") or name in ("", ".", ".."):
        raise ValueError("Ungültiger Dateipfad.")
    parent = parent or "/"
    here = _q("./" + name)
    up = _q("../" + name)
    source = _q(quarantine_path)
    stage = _q("" if parent == "/" else parent) + '/"${T#./}"'
    leave = 'cd .. && rmdir "$T" 2>/dev/null || true'
    # Nach gescheiterter Prüfung steht die Shell evtl. schon im Zwischenordner (oder, bei einem untergeschobenen Link, ganz woanders):
    # zurück in den Ursprungsordner, dort nur den eigenen (leeren) Zwischenordner entfernen.
    drop = f'cd {_q(parent)} 2>/dev/null && rmdir "$T" 2>/dev/null || true'
    stage_failed = f"{{ {drop}; echo {_q(RESTORE_STAGE_FAILED)}; exit 5; }}"
    return (
        f"set -e; [ -f {source} ] || {{ echo 'Nicht mehr in der Quarantäne'; exit 3; }}; "
        f"cd -P {_q(parent)} 2>/dev/null || {{ echo {_q(RESTORE_FOLDER_MISSING)}; exit 3; }}; "
        f"[ \"$(pwd -P)\" = {_q(parent)} ] || {{ echo {_q(RESTORE_LINK_REFUSED)}; exit 5; }}; "
        f"if [ -e {here} ] || [ -L {here} ]; then echo 'Am Ursprungsort liegt inzwischen eine andere Datei'; exit 4; fi; "
        f"T=$(mktemp -d {RESTORE_STAGE_TEMPLATE} 2>/dev/null) || {{ echo {_q(RESTORE_STAGE_FAILED)}; exit 5; }}; "
        f"cd -P \"$T\" 2>/dev/null && [ \"$(pwd -P)\" = {stage} ] "
        f"&& case \"$(stat -c %u:%a .)\" in \"$(id -u):700\"|\"$(id -u):2700\") true;; *) false;; esac "
        f"|| {stage_failed}; "
        f"chmod {safe_mode} {source}; mv -T -n {source} {here} 2>/dev/null || true; "
        f"if [ -e {source} ] || [ -L {source} ]; then chmod 000 {source}; {leave}; echo {_q(RESTORE_NOT_MOVED)}; exit 4; fi; "
        f"mv -T -n {here} {up} 2>/dev/null || true; "
        f"if [ -e {here} ] || [ -L {here} ]; then mv -T -n {here} {source} 2>/dev/null || true; "
        f"if [ -f {source} ]; then chmod 000 {source}; {leave}; echo {_q(RESTORE_TARGET_TAKEN)}; exit 4; fi; "
        f"echo {_q(RESTORE_STUCK)} \"$(pwd -P)\"; exit 4; fi; "
        f"{leave}; echo ok"
    )


def delete_command(quarantine_path: str) -> str:
    if not quarantine_path.startswith(QUARANTINE_DIR + "/") or ".." in quarantine_path:
        raise ValueError("Nur Dateien aus der Quarantäne dürfen gelöscht werden.")
    return f"rm -f {_q(quarantine_path)}; echo ok"


def parse_mode(output: str) -> str | None:
    m = re.search(r"@@mode=([0-7]{3,4})", output)
    return m.group(1) if m else None


def quarantine_name(finding_id: str, path: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", path.rsplit("/", 1)[-1])[:80] or "datei"
    return f"{finding_id}_{base}"


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
    # "AUTH-9286|Configure maximum password age|-|-|" -> "AUTH-9286: Configure maximum password age"
    # "SSH-7408|Consider hardening SSH configuration|MaxAuthTries (set 6 to 3)|-|"
    #   -> "SSH-7408: Consider hardening SSH configuration (MaxAuthTries (set 6 to 3))"
    # Die Angabe im dritten Feld sagt, was genau gemeint ist; ohne sie wären mehrere SSH-Hinweise wortgleich.
    # Das Format ist "ID|Text|Angabe|Lösung|". Die Angabe kann selbst "|" enthalten (Lynis maskiert das nicht), z. B.
    # "PermitRootLogin (set YES to (FORCED-COMMANDS-ONLY|NO|PROHIBIT-PASSWORD|WITHOUT-PASSWORD))": dann ist sie alles
    # zwischen dem Text und der Lösung.
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
        return LynisResult(status="error", error=NO_ROOT_MESSAGE)
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
    # Danach bleibt das automatische Signatur-Update eingeschaltet (`enable --now`): Ein nur gestarteter Dienst ist nach dem nächsten
    # Neustart wieder aus.
    "signatures": (
        "systemctl stop clamav-freshclam 2>/dev/null; freshclam --quiet; R=$?; "
        "systemctl enable --now clamav-freshclam 2>/dev/null; exit $R"
    ),
}
