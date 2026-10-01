"""Virenschutz (Nachfolger der Virenschutz-Skripte des Vorgaengersystems, inklusive
der Quarantaene-Funktion `isolate_file_to_quarantine`).

Alles laeuft per SSH auf dem jeweiligen Server -- ClamAV und Lynis werden dort
aufgerufen, nicht auf dem Dashboard-Host. Dieses Modul enthaelt nur die REINEN Teile:
Befehle bauen und Ausgaben auswerten. Ausfuehren, Speichern und Melden steht in
`defender.py`.

Unterschiede zum Original, bewusst:
- Die Quarantaene liegt auf dem betroffenen Server selbst (`QUARANTINE_DIR`), nicht
  auf dem Rechner des alten Skripts -- das Original verschob per `shutil.move` nur LOKALE Pfade und
  lief fuer Funde auf anderen Servern still ins Leere.
- Wiederherstellen setzt die urspruenglichen Dateirechte zurueck (Original: immer 644).
- Der "Echtzeit-Waechter" ist ein kurzer, wiederkehrender Scan aller in den letzten
  Minuten geaenderten Dateien der ueberwachten Ordner -- inotify auf entfernten
  Servern wuerde einen eigenen Agenten auf jedem Server verlangen.
"""

from __future__ import annotations

import re
import secrets
import shlex
from dataclasses import dataclass, field

QUARANTINE_DIR = "/var/lib/nexus-quarantine"

QUICK_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root"]
DEEP_PATHS = ["/"]
WATCH_PATHS = ["/tmp", "/var/tmp", "/dev/shm", "/home", "/root", "/opt"]
# Nur fuer den Waechter (Schnell-/Tiefenscan pruefen /dev/shm weiter): die IPC-Dateien
# von pmxcfs/corosync auf Proxmox aendern sich staendig -- sonst liefe clamscan (laedt
# jedes Mal die ganze Datenbank) bei jedem Waechterlauf.
WATCH_EXCLUDED = ["/dev/shm/qb-*"]

# clamdscan meldet eine zwischen `find` und Scan verschwundene Datei mit Rueckgabecode 2 und
# "ERROR: Can't access file ..." (ClamAV 1.0.5), die uebrigen Dateien sind trotzdem geprueft.
# Erweitertes grep-Muster fuer die Zeilen, die dabei erlaubt sind: genau diese Meldung und Funde.
_CLAMD_SKIP_OK = "^(ERROR|WARNING): Can.t access file | FOUND$"

# Tiefenscan: virtuelle und fluechtige Dateisysteme nie durchsuchen.
_ALWAYS_EXCLUDED = ["/proc", "/sys", "/dev", "/run", QUARANTINE_DIR]

# Rueckgabecode-Marke eines Scans: je Lauf eine Zufallsmarke `@@scan-rc-<16 hex>=` (Fix N1). Eine
# feste Marke liesse sich in einen Dateinamen schreiben (`/tmp/x@@nexus-rc=0`), und die
# Auswertung schnitte die Ausgabe dort ab -- der Fund waere verschwunden. Die Marke wird beim
# Befehlsbau gezogen und im selben Prozess bei der Auswertung wieder gebraucht
# (`build_*_command(..., mark=m)` und `parse_scan_output(..., m)`).
_RC_MARK_RE = re.compile(r"@@scan-rc-[0-9a-f]{16}=")
NO_ROOT = "@@noroot"
NO_ROOT_MESSAGE = (
    "Keine root-Rechte: Das Dashboard ist auf diesem Server nicht als root angemeldet und "
    "darf sudo nicht ohne Passwort nutzen."
)


def as_root(command: str, *, required: bool = True) -> str:
    """Fuehrt `command` als root aus: direkt, wenn schon root, sonst ueber `sudo -n`
    (ohne Passwortabfrage). Geht beides nicht: bei `required` Abbruch mit NO_ROOT,
    sonst als normaler Benutzer (ein Scan ohne root prueft dann nur lesbare Dateien)."""
    inner = shlex.quote(command)
    fallback = f"sh -c {inner}" if not required else f"echo '{NO_ROOT}'; exit 126"
    return (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {inner}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {inner}; "
        f"else {fallback}; fi"
    )


def new_rc_mark() -> str:
    """Frische Zufallsmarke fuer einen Scan-Lauf: `@@scan-rc-<16 hex>=`."""
    return f"@@scan-rc-{secrets.token_hex(8)}="


def _checked_mark(mark: str | None) -> str:
    """Die Marke kommt in doppelte Anfuehrungszeichen der Shell: nur genau dieses Format."""
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
    # Anzahl Funde: mindestens die gelesenen Fund-Zeilen, sonst die Zahl aus der ClamAV-Zusammenfassung
    # oder 1, wenn ClamAV mit Code 1 endet, obwohl keine Fund-Zeile lesbar war.
    infected: int = 0
    # Die Fund-Zeilen sind nicht verlaesslich (Zeilenumbruch im Dateinamen, Zeilen die ClamAV nie
    # ausgibt, Zahlen die nicht zusammenpassen, Marke fehlt): `findings` kann dann gefaelschte Pfade
    # enthalten. Der Defender verschiebt in dem Fall nichts automatisch (er liefe als root).
    unreliable: bool = False


def _q(value: str) -> str:
    return shlex.quote(value)


def build_scan_command(
    paths: list[str], *, max_filesize_mb: int = 50, exclude: list[str] | None = None, mark: str | None = None,
) -> str:
    """`clamscan` rekursiv, nur Funde ausgeben (-i), Zusammenfassung am Ende fuer die
    Dateizahl. Rueckgabecode wird angehaengt, weil 1 hier "Fund" heisst, nicht Fehler.

    `mark`: die Zufallsmarke dieses Laufs (`new_rc_mark()`), dieselbe geht spaeter an
    `parse_scan_output`. Ohne Angabe wird eine neue gezogen."""
    mark = _checked_mark(mark)
    excluded = [*_ALWAYS_EXCLUDED, *(exclude or [])]
    parts = ["clamscan", "-r", "-i", "--stdout", "--cross-fs=no" if paths == ["/"] else "",
             f"--max-filesize={max_filesize_mb}M", f"--max-scansize={max_filesize_mb * 4}M"]
    parts += [f"--exclude-dir={_q('^' + p.replace('.', chr(92) + '.'))}" for p in excluded]
    parts += [_q(p) for p in paths]
    cmd = " ".join(p for p in parts if p)
    return f"{cmd} 2>&1; echo \"{mark}$?\""


def build_watch_command(
    paths: list[str], *, minutes: int, max_filesize_mb: int = 50, exclude: list[str] | None = None,
    use_clamd: bool = False, mark: str | None = None,
) -> str:
    """Nur Dateien, die in den letzten `minutes` Minuten neu oder geaendert wurden --
    der kurze Waechter-Lauf. Ohne Treffer wird clamscan gar nicht erst gestartet.

    Die Dateiliste darf nicht selbst ein Treffer sein: mktemp legt sie ohne TMPDIR
    (ueber SSH/sudo nie gesetzt) in /tmp an, und /tmp wird ueberwacht -- dann liefe
    clamscan (laedt jedes Mal die ganze Datenbank) bei jedem Lauf. Deshalb nach /run
    (als root beschreibbar, nie ueberwacht) und die Liste zusaetzlich ausschliessen
    (mktemp-Namen enthalten keine Glob-Zeichen).

    `exclude` sind find-Muster (Standard: WATCH_EXCLUDED), die nie ausloesen.

    `use_clamd` (Einstellung "Echtzeit-Waechter mit clamdscan"; Standard aus):
    Antwortet ein laufender clamd (`clamdscan --ping 1`), prueft der Waechter ueber ihn
    (`clamdscan --fdpass`, die Datenbank ist dort schon geladen), sonst -- oder wenn
    clamdscan dann doch scheitert -- wie bisher mit clamscan. Die Ausgabe wird gleich
    ausgewertet (`parse_scan_output`): Funde als "Pfad: Name FOUND", dazu `Scanned files: N`
    (aus der Dateiliste, weil clamdscan ohne Zusammenfassung laeuft) und der
    Rueckgabecode. Ohne `use_clamd` bleibt der Befehl Zeichen fuer Zeichen wie bisher.

    Rueckgabecode 1 und 2 von clamdscan gelten nur als ordentlicher Lauf, wenn die Ausgabe nur
    aus "Can't access file"-Zeilen und Fund-Zeilen besteht (`_CLAMD_SKIP_OK`): Eine Datei ist
    zwischen `find` und Scan verschwunden -- in /tmp und /dev/shm Alltag --, die uebrigen sind
    geprueft. Ein clamscan-Neustart (Datenbank laden, rund 20 s und 1 GB) waere genau das,
    was die Einstellung sparen soll. Jede andere Ausgabe mit Code 1 oder 2, auch eine leere,
    fuehrt zum Rueckfall (bei Code 1 plus Verbindungsfehler wuerden sonst die Dateien nach
    dem Fund ungeprueft als "geprueft" zaehlen; clamscan findet den Fund erneut).

    `mark`: die Zufallsmarke dieses Laufs (`new_rc_mark()`), wie bei `build_scan_command`."""
    mark = _checked_mark(mark)
    finds = " ".join(_q(p) for p in paths)
    skip = " ".join(f"-not -path {_q(p)}" for p in (WATCH_EXCLUDED if exclude is None else exclude))
    clamscan = 'clamscan -i --stdout --file-list="$L" 2>&1; R=$?'
    if use_clamd:
        # Nur ein sauberer Lauf von clamdscan zaehlt: Rueckgabecode 0, oder 1/2 mit nichts als
        # verschwundenen Dateien und Funden in der Ausgabe (clamdscan meldet bei einem Fund
        # Code 1, auch wenn danach Verbindungsfehler kamen). Alles andere (clamd zwischen Ping
        # und Scan weg, Verbindungsfehler) wird verworfen und mit clamscan wiederholt --
        # ein misslungener Scan darf nie als "sauber" durchgehen.
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
        f"L=$(mktemp -p /run 2>/dev/null || mktemp); "
        f"find {finds} -xdev -type f -mmin -{int(minutes)} -size -{int(max_filesize_mb)}M "
        f"-not -path {_q(QUARANTINE_DIR + '/*')} {skip} -not -path \"$L\" 2>/dev/null | head -n 2000 > \"$L\"; "
        f"if [ -s \"$L\" ]; then {scan}; "
        f"else echo 'Scanned files: 0'; R=0; fi; rm -f \"$L\"; echo \"{mark}$R\""
    )


_FOUND_RE = re.compile(r"^(?P<path>/.*): (?P<sig>.+) FOUND$")
_SCANNED_RE = re.compile(r"^Scanned files: ([0-9]+)")
_INFECTED_RE = re.compile(r"^Infected files: ([0-9]+)")
# Die Meldung der Shell selbst ("sh: 1: clamscan: not found", "bash: line 1: clamscan: command not found").
_NOT_INSTALLED_RE = re.compile(r"^(?:/(?:usr/)?bin/)?(?:ba|da|a)?sh: (?:(?:line )?[0-9]+: )?clamscan: (?:command )?not found$")
NOT_INSTALLED_MESSAGE = "ClamAV ist auf diesem Server nicht installiert."


def parse_scan_output(output: str, mark: str | None = None) -> ScanResult:
    """Wertet die Ausgabe von `build_scan_command` / `build_watch_command` aus.

    Die Ausgabe enthaelt Dateinamen vom gescannten Server, und ClamAV schreibt sie unveraendert hin
    (mit ClamAV 1.0.5 geprueft: Zeilenumbruch, Wagenruecklauf, Seitenvorschub und ESC im Namen kommen
    roh an). Ein Name darf die Auswertung deshalb nie steuern:

    - `mark` ist die Marke dieses Laufs. Es zaehlt der LETZTE Treffer am Zeilenanfang: Die echte Marke
      steht als Allerletztes in der Ausgabe, ein Dateiname mit Marke darin (oder mit Zeilenumbruch
      davor) steht immer davor und kann nichts verstecken. Alles nach der Marke fliegt raus, alles
      davor wird ausgewertet. Ohne `mark` gilt jede Marke im Format `@@scan-rc-<16 hex>=`; die alte
      feste Marke `@@nexus-rc=` zaehlt nie. Fehlt die Marke ganz, wurde der Lauf nicht ordentlich
      beendet: Fehler, nie "sauber".
    - Zeilen werden nur am Zeilenvorschub getrennt, nicht mit `str.splitlines()`: das trennt auch bei
      Wagenruecklauf, Seitenvorschub, U+0085, U+2028 und mehr, und eine Fund-Zeile mit so einem
      Zeichen im Namen fiele auseinander (der Fund wuerde unsichtbar, der Lauf "sauber").
    - Mit Zeilenumbruch im Dateinamen zerfaellt ein Fund in mehrere Zeilen, und die letzte kann
      wie ein Fund fuer einen ganz anderen Pfad aussehen (`/etc/passwd: Sig FOUND`). Solche Laeufe
      sind `unreliable`: eine Zeile mit Pfad am Anfang, die kein Fund ist (der Anfang des zerrissenen
      Namens, mit `-i` schreibt ClamAV Pfade nur in Fund-Zeilen), oder eine Zahl von Fund-Zeilen, die
      nicht zu `Infected files:` passt (die Zusammenfassung steht nach allen Namen, ihr letzter
      Treffer ist deshalb echt). Ein Fund bleibt trotzdem ein Fund.
    - Endet ClamAV mit Code 1, ist es mindestens `infected`, auch wenn keine Zeile lesbar war;
      die Meldung "ClamAV nicht installiert" gilt nur als ganze Zeile der Shell oder bei Code 127,
      nie als Teilstueck eines Dateinamens."""
    pattern = _RC_MARK_RE.pattern if mark is None else re.escape(_checked_mark(mark))
    matches = list(re.finditer(r"^" + pattern + r"([0-9]+)[ \t\r]*$", output, re.MULTILINE))
    rc_match = matches[-1] if matches else None
    rc = int(rc_match.group(1)) if rc_match else None
    body = output[: rc_match.start()] if rc_match else output

    if rc == 127:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)

    findings: list[tuple[str, str]] = []
    errors: list[str] = []
    found_lines = 0  # Zeilen, die auf " FOUND" enden (auch unlesbare und eingeschmuggelte)
    stray = 0  # Zeilen mit Pfad am Anfang, die kein Fund sind
    files: int | None = None
    summary_infected: int | None = None
    shell_not_found = False
    nonblank: list[str] = []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        nonblank.append(line)
        is_found_line = line.endswith(" FOUND")
        found_lines += int(is_found_line)
        # Die Regex nur auf Zeilen, die wie ein Fund enden: auf langen Bruchstuecken mit vielen ": "
        # (Dateiname mit Zeilenumbruch) wuerde sie sonst quadratisch zuruecksetzen und die Auswertung
        # -- und damit das ganze Dashboard -- sekundenlang blockieren.
        m = _FOUND_RE.match(line) if is_found_line else None
        if m:
            findings.append((m.group("path"), m.group("sig")))
        elif line.startswith("/"):
            # Mit `-i` schreibt ClamAV einen Pfad nur in Fund-Zeilen. Der Anfang eines Fundes mit
            # Zeilenumbruch im Namen ("/tmp/q") beginnt immer mit dem Pfad des gescannten Ordners.
            stray += 1
            shell_not_found = shell_not_found or bool(_NOT_INSTALLED_RE.match(line))
        elif line.startswith(("ERROR:", "LibClamAV Error")):
            errors.append(line)
        elif (summary := _SCANNED_RE.match(line)) is not None:
            files = int(summary.group(1))  # der LETZTE Treffer gilt: die echte Zusammenfassung steht nach allen Namen
        elif (summary := _INFECTED_RE.match(line)) is not None:
            summary_infected = int(summary.group(1))
        else:
            shell_not_found = shell_not_found or bool(_NOT_INSTALLED_RE.match(line))

    infected_count = max(len(findings), summary_infected or 0, 1 if rc == 1 else 0)
    if shell_not_found and not infected_count:
        return ScanResult(status="error", error=NOT_INSTALLED_MESSAGE)

    if infected_count:
        unreliable = (
            rc is None or stray > 0 or len(findings) != found_lines
            or (summary_infected is not None and summary_infected != found_lines)
            or len(findings) < infected_count
        )
        return ScanResult(
            status="infected", files_scanned=files, findings=findings, errors=errors,
            infected=infected_count, unreliable=unreliable,
        )
    if rc == 0:
        return ScanResult(status="clean", files_scanned=files, errors=errors)
    if rc == 2 and files:
        # Einzelne Dateien/Ordner nicht lesbar (meist fehlende Rechte), der Rest ist sauber.
        return ScanResult(status="clean", files_scanned=files, errors=errors)
    if rc is None:
        return ScanResult(
            status="error", files_scanned=files, errors=errors,
            error="Scan fehlgeschlagen: Die Ausgabe ist unvollständig (der Lauf wurde nicht ordentlich beendet).",
        )
    detail = errors[0] if errors else (nonblank[-1] if nonblank else "unbekannter Fehler")
    return ScanResult(status="error", files_scanned=files, errors=errors, error=f"Scan fehlgeschlagen: {detail}")


STATUS_COMMAND = (
    "echo @@clam; (clamscan --version 2>/dev/null || echo none); "
    # Kein `|| echo unknown`: bei einem gestoppten Dienst gibt systemctl "inactive" mit rc 3
    # aus -- dahinter stand dann noch "unknown", und "aus" wurde nie erkannt.
    "echo @@fresh; (systemctl is-active clamav-freshclam 2>/dev/null; true); "
    # `lynis --version` endet sofort; `lynis show version` legte erst den Bericht neu an
    # (und raeumte die PID-Datei eines laufenden Audits weg).
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
    # Nur die erste Zeile zaehlt (active | inactive | failed | ...); leer = unbekannt
    # (kein systemd).
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


def quarantine_command(path: str, quarantine_name: str) -> str:
    """Verschiebt die Datei in den Tresor, merkt sich die Rechte (Ausgabe) und macht
    sie unlesbar/unausfuehrbar. Bricht ab, wenn die Datei nicht (mehr) existiert.

    Symbolischen Links wird nie gefolgt: `[ -f ]` und `chmod` folgen ihnen, `mv`
    verschiebt nur den Link -- als root traefe chmod 000 sonst das Linkziel (z. B.
    /etc/shadow). Ebenso darf kein Ordner im Pfad inzwischen ein Link sein (sonst
    wandert die echte /etc/shadow in den Tresor). Deshalb erst in den Ordner wechseln
    (`cd -P`, danach aendert ein Tausch des Ordners nichts mehr), dort nur mit dem
    Dateinamen arbeiten, und nach dem mv noch einmal pruefen (Tausch zwischen Pruefung
    und mv; im Tresor, nur fuer root, kann niemand mehr tauschen)."""
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
        f"mkdir -p {QUARANTINE_DIR}; chmod 700 {QUARANTINE_DIR}; "
        f"M=$(stat -c %a {here}); mv -f {here} {target}; "
        f"if [ -L {target} ]; then rm -f {target}; echo {_q(SYMLINK_SWAPPED)}; exit 5; fi; "
        f"chmod 000 {target}; echo \"@@mode=$M\""
    )


def restore_command(quarantine_path: str, original_path: str, mode: str | None) -> str:
    """Zurueck an den Ursprungsort -- wie beim Verschieben nie ueber einen Ordner, der
    inzwischen eine Verknuepfung ist (als root, mit Besitzer und Rechten der Datei,
    laege sie sonst z. B. in /etc/profile.d)."""
    safe_mode = mode if mode and re.fullmatch(r"[0-7]{3,4}", mode) else "644"
    parent, _, name = original_path.rpartition("/")
    if not original_path.startswith("/") or name in ("", ".", ".."):
        raise ValueError("Ungültiger Dateipfad.")
    parent = parent or "/"
    here = _q("./" + name)
    return (
        f"set -e; [ -f {_q(quarantine_path)} ] || {{ echo 'Nicht mehr in der Quarantäne'; exit 3; }}; "
        f"mkdir -p {_q(parent)}; cd -P {_q(parent)} 2>/dev/null || {{ echo 'Ursprungsordner nicht erreichbar'; exit 3; }}; "
        f"[ \"$(pwd -P)\" = {_q(parent)} ] || {{ echo {_q(RESTORE_LINK_REFUSED)}; exit 5; }}; "
        f"if [ -e {here} ] || [ -L {here} ]; then echo 'Am Ursprungsort liegt inzwischen eine andere Datei'; exit 4; fi; "
        f"chmod {safe_mode} {_q(quarantine_path)}; mv {_q(quarantine_path)} {here}; echo ok"
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

LYNIS_COMMAND = (
    "if ! command -v lynis >/dev/null 2>&1; then echo '@@nolynis'; exit 0; fi; "
    "lynis audit system --quick --quiet --no-colors >/dev/null 2>&1; "
    "grep -E '^(warning\\[\\]|suggestion\\[\\]|hardening_index)=' /var/log/lynis-report.dat 2>/dev/null; echo '@@done'"
)


@dataclass
class LynisResult:
    status: str  # ok | error
    hardening_index: int | None = None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    error: str | None = None


def _clean_lynis(entry: str) -> str:
    # "AUTH-9286|Configure maximum password age|-|-|" -> "AUTH-9286: Configure maximum password age"
    parts = [p for p in entry.split("|") if p and p != "-"]
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
        # Debian 12 hat kein /var/log/auth.log mehr -- ohne systemd-Backend startet der sshd-Jail nicht.
        "if [ ! -f /etc/fail2ban/jail.local ]; then printf '[DEFAULT]\\nbackend = systemd\\n\\n[sshd]\\nenabled = true\\n' > /etc/fail2ban/jail.local; fi; "
        "systemctl enable --now fail2ban 2>/dev/null; systemctl restart fail2ban 2>/dev/null; fail2ban-client ping"
    ),
    "unattended": (
        "command -v apt-get >/dev/null || { echo 'Nur für Debian/Ubuntu (apt)'; exit 2; }; "
        "DEBIAN_FRONTEND=noninteractive apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q unattended-upgrades && "
        "printf 'APT::Periodic::Update-Package-Lists \"1\";\\nAPT::Periodic::Unattended-Upgrade \"1\";\\n' > /etc/apt/apt.conf.d/20auto-upgrades && echo ok"
    ),
    # Danach bleibt das automatische Signatur-Update eingeschaltet (`enable --now`) --
    # ein nur gestarteter Dienst ist nach dem naechsten Neustart wieder aus.
    "signatures": (
        "systemctl stop clamav-freshclam 2>/dev/null; freshclam --quiet; R=$?; "
        "systemctl enable --now clamav-freshclam 2>/dev/null; exit $R"
    ),
}
