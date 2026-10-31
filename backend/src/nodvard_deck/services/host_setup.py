"""Einrichtungsbefehl für einen Server: Benutzer anlegen, Schlüssel eintragen, auf Wunsch Gruppen und sudo ohne Passwort.

Reine Funktionen ohne I/O; mit Passwort führt das Dashboard das Skript selbst aus (`remote_setup_command`)."""

from __future__ import annotations

import base64
import hashlib
import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import asyncssh
from nodvard_sdk import ROOT_HELPER_DIR

from ..core import ssh
from ..ext.root_helpers import RootHelperFile

# Der Befehl wird von Hand auf dem Server ausgeführt, deshalb kommt nichts Ungeprüftes ins Skript: Benutzer, Gruppen und
# öffentlicher Schlüssel werden streng per Zeichenliste geprüft (Fehlermeldungen nennen den Wert nie),
# Anführungszeichen, `$`, Backtick, Backslash und Zeilenumbruch kommen nicht durch (`_dq`), das ganze Skript wird für
# `sh -c` mit `shlex.quote` verpackt. Es enthält kein einfaches Hochkomma, lässt sich also in `sh -c '...'` einbetten.
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
"""Benutzer auf dem Server (Linux-Namensregel). Strenger als der Benutzername, den das Dashboard
für Windows-Zugänge speichern darf: Hier wird er in ein Shell-Skript eingesetzt."""
GROUP_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

_PUBLIC_KEY_RE = re.compile(
    r"(?P<alg>ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521))"
    r" (?P<blob>[A-Za-z0-9+/]+={0,2})"
    r"(?: (?P<comment>[A-Za-z0-9_.@-]{1,100}))?"
)

_USER_ERROR = "Benutzer auf dem Server: nur Kleinbuchstaben, Ziffern, _ und - (höchstens 32 Zeichen, nicht mit einer Ziffer oder - beginnen)."
_GROUP_ERROR = "Ungültiger Gruppenname."
_KEY_ERROR = "Der öffentliche Schlüssel hat kein gültiges Format."
_FORBIDDEN_IN_DOUBLE_QUOTES = set("\"'$`\\!")


_HELPER_ID_RE = re.compile(r"[a-z][a-z0-9-]{1,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

KEY_ADDED = "Schlüssel für Nodvard Deck eingetragen."
"""Meldung des Skripts, wenn es den Schlüssel neu in `authorized_keys` schreibt (steht er schon dort, kommt sie nicht)."""
KEY_FIXED = "Schlüssel für Dropbear angepasst."
"""Meldung des Skripts, wenn es bei Dropbear die eigene alte Zeile `restrict,pty <Schlüssel>` durch eine ersetzt, die
Dropbear versteht."""
KEY_RESTRICTED = "Der schon eingetragene Schlüssel für Nodvard Deck ist jetzt eingeschränkt (kein Weiterleiten von Ports, kein Agent)."
"""Meldung des Skripts, wenn der Schlüssel ohne Optionen in `authorized_keys` stand und sie jetzt davor stehen."""

# Dropbear verwirft eine Zeile mit `pty` ganz und gibt mit `restrict` kein Terminal; die Liste für Dropbear verstehen
# beide Dienste, sie gilt deshalb, sobald ein Dropbear läuft. Auf OpenSSH fehlt ihr gegenüber `restrict` nur
# `no-user-rc` (~/.ssh/rc legt nur das Konto selbst an), ein Prozess, der sich dropbear nennt, bringt also keine Rechte.
# Das Banner der Verbindung braucht es nicht: Beim Einrichten mit Passwort bedient ein Prozess dropbear die Sitzung.
OPENSSH_KEY_OPTIONS = "restrict,pty"
DROPBEAR_KEY_OPTIONS = "no-port-forwarding,no-agent-forwarding,no-X11-forwarding"
_PROC_COMMS = "/proc/[0-9]*/comm"
# KO: Optionen vor dem Schlüssel; DB: ein Prozess dropbear läuft. Einer in einem anderen PID-Namensraum (Container
# auf dem Rechner) zählt nicht; lässt sich der Namensraum nicht lesen, zählt er. Nur Builtins, so geht es in dash und
# busybox sh.
_KEY_OPTIONS = (
    f'KO="{OPENSSH_KEY_OPTIONS}"; DB=; for KP in {_PROC_COMMS}; do IFS= read -r KL 2>/dev/null < "$KP" || continue; '
    'case "$KL" in dropbear*) KN="${KP%/comm}/ns/pid"; KS="${KP%/*/comm}/self/ns/pid"; '
    '[ -e "$KN" ] && [ -e "$KS" ] && ! [ "$KN" -ef "$KS" ] && continue; '
    f'DB=1; KO="{DROPBEAR_KEY_OPTIONS}"; break;; esac; done'
)
# KM: KL ist eine eigene alte Zeile, also `restrict,pty $K` oder derselbe Schlüssel mit einem anderen eigenen Kommentar
# (älteres `lattice@…`, Server umbenannt); nur die erste bekommt `$KO` statt `restrict,pty`. KN: KL ist der Schlüssel
# ohne Optionen (etwa von Hand eingetragen); jede solche Zeile bekommt `$KO` davor. Leerraum ist wie bei sshd nur
# Leerzeichen und Tab (KZ), am Ende darf ein CR (KQ) einer Windows-Zeile stehen.
# KF <KM|KN> [Aufruf]: schreibt die Datei mit den so geänderten Zeilen neu, alle anderen bleiben Byte für Byte; die Länge
# zeigt, ob `read` etwas verloren hat (etwa ein NUL-Byte). Lesen und Schreiben laufen mit dem Aufruf davor (`KF KM R`:
# als der Benutzer). `tee` schreibt in dieselbe Datei, so bleiben Besitz, Rechte und ein Link. KC: 1 nichts geändert,
# 2 Schreiben nicht bestätigt (alter Inhalt neu geschrieben), 3 keine passende Zeile.
_KEY_FIX_FUNCTION = (
    'KZ=" $(printf "\\t")"; KQ=$(printf "\\r"); '
    'KM() { [ -z "$KX" ] || return 1; case "$KL" in "restrict,pty $K") ;; "restrict,pty $B "*) '
    'case "${KL##* }" in *[!A-Za-z0-9_.@-]*) return 1;; '
    'nodvard@*|lattice@*) [ "$KL" = "restrict,pty $B ${KL##* }" ] || return 1;; *) return 1;; esac;; '
    '*) return 1;; esac; KL="$KO ${KL#* }"; }; '
    'KN() { KV=${KL#"${KL%%[!$KZ]*}"}; KA=${KV%%[$KZ]*}; [ "$KA" = "${B%% *}" ] || return 1; KR=${KV#"$KA"}; '
    'KG=${KR%%[!$KZ]*}; [ -n "$KG" ] || return 1; KR=${KR#"$KG"}; '
    'case "$KR" in "${B#* }"|"${B#* }"[$KZ$KQ]*) KL="$KO $KV";; *) return 1;; esac; }; '
    'KF() { KC=1; KW=$1; shift; KT=$(mktemp -d) || return 1; if "$@" cat "$A" > "$KT/a"; then KX=; KD=0; '
    'while IFS= read -r KL || [ -n "$KL" ]; do KS=$KL; if $KW; then KX=1; KD=$(( KD + ${#KL} - ${#KS} )); fi; '
    'printf "%s\\n" "$KL"; done < "$KT/a" > "$KT/b"; '
    'if [ -z "$KX" ]; then KC=3; '
    'elif [ $(( $(wc -c < "$KT/b") - $(wc -c < "$KT/a") )) -eq "$KD" ]; then '
    'if "$@" tee "$A" < "$KT/b" > /dev/null && "$@" cmp -s - "$A" < "$KT/b"; then KC=0; '
    'else "$@" tee "$A" < "$KT/a" > /dev/null; KC=2; fi; fi; fi; rm -f "$KT/a" "$KT/b"; rmdir "$KT"; return $KC; }'
)


def _key_present(run: str) -> str:
    """Erfolg, wenn eine Zeile mit dem Schlüssel kein Kommentar ist; `run` ist der Aufruf davor (`R ` ohne root)."""
    # Wer die Zeile auskommentiert hat, um Nodvard Deck zu sperren, bekommt beim Einrichten wieder einen Schlüssel.
    # `-a`: Mit einem NUL-Byte oder fremder Zeichenkodierung in der Datei gäbe grep sonst keine Zeile aus.
    return f'{run}grep -aF -- "$B" "$A" 2>/dev/null | grep -qv -- "^[$KZ]*#"'


def _key_restrict(run: str) -> str:
    """Setzt `$KO` vor jede Zeile mit dem Schlüssel ohne Optionen; `run` ist der Aufruf davor (`R ` ohne root)."""
    # Die Vorprüfung trifft mindestens jede Zeile, die KN trifft; ein `.` in der Art passt dabei auf jedes Zeichen.
    return (
        f'if {run}grep -q -- "^[$KZ]*${{B%% *}}[$KZ][$KZ]*${{B#* }}" "$A" 2>/dev/null; then '
        'if ! command -v cmp >/dev/null; then echo "FEHLER: In $A steht der Schlüssel von Nodvard Deck ohne '
        'Einschränkung, und zum sicheren Anpassen fehlt hier cmp (Paket diffutils). Die Datei ist unverändert."; '
        'echo "Hinweis: diffutils installieren und noch einmal einrichten, oder von Hand vor den Schlüssel von Nodvard '
        'Deck „$KO“ und ein Leerzeichen setzen."; exit 1; '
        f'elif KF KN {run}; then echo "{KEY_RESTRICTED}"; '
        'elif [ "$KC" = 2 ]; then echo "FEHLER: $A ließ sich beim Einschränken des Schlüssels nicht sicher schreiben. '
        'Nodvard Deck hat versucht, den alten Inhalt zurückzuschreiben; bitte die Datei prüfen."; exit 1; '
        'elif [ "$KC" != 3 ]; then echo "FEHLER: In $A steht der Schlüssel von Nodvard Deck ohne Einschränkung, und die '
        'Zeile ließ sich nicht anpassen (Datei nicht lesbar oder mit ungewöhnlichen Zeichen), sie ist unverändert."; '
        'echo "Hinweis: Von Hand beheben: in dieser Datei vor den Schlüssel von Nodvard Deck „$KO“ und ein Leerzeichen '
        'setzen, dann „Verbindung prüfen“."; exit 1; fi; fi'
    )


def _key_fix(run: str) -> str:
    """Repariert bei Dropbear die eigene alte Zeile; `run` ist der Aufruf davor (`R ` ohne root)."""
    return (
        f'if [ -n "$DB" ] && {run}grep -qF -- "restrict,pty $B" "$A" 2>/dev/null; then '
        f'if KF KM {run}; then echo "{KEY_FIXED}"; '
        'elif [ "$KC" = 2 ]; then echo "FEHLER: $A ließ sich beim Anpassen für Dropbear nicht sicher schreiben. Nodvard '
        'Deck hat versucht, den alten Inhalt zurückzuschreiben; bitte die Datei prüfen."; exit 1; '
        'elif [ "$KC" != 3 ]; then echo "FEHLER: Die Schlüsselzeile in $A ließ sich nicht für Dropbear anpassen '
        '(Datei nicht lesbar oder mit ungewöhnlichen Zeichen), sie ist unverändert."; '
        'echo "Hinweis: Von Hand beheben: in dieser Datei vor dem Schlüssel von Nodvard Deck „restrict,pty“ durch '
        f'„{DROPBEAR_KEY_OPTIONS}“ ersetzen, dann „Verbindung prüfen“."; exit 1; fi; fi'
    )


SETUP_ACCOUNT_FILES = ("/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow")
"""Konto-Dateien, die das Skript beim Anlegen des Benutzers und beim Eintragen in Gruppen ändern kann."""

STATE_MARK = "@@nodvard-stand"
# `ST before|after DATEI`: Prüfsumme, Rechte und Besitzer genau wie der Datei-Wächter von Nodvard Shield sie erfasst
# (`sha256sum`, `stat -c "%a %U"`), „- - -“ für eine fehlende Datei. Fehlt ein Werkzeug oder ist die Datei nicht lesbar,
# meldet es nichts; dann übernimmt der Wächter auch nichts still.
_STATE_FUNCTION = (
    'ST() { if ! command -v sha256sum >/dev/null 2>&1 || ! command -v stat >/dev/null 2>&1; then :; '
    f'elif [ ! -e "$2" ]; then echo "{STATE_MARK} $1 - - - $2"; '
    f'elif [ -f "$2" ] && [ -r "$2" ]; then echo "{STATE_MARK} $1 $(sha256sum < "$2" | cut -d" " -f1) '
    '$(stat -c "%a %U" "$2") $2"; fi; }'
)
_HELPER_DIR_RE = re.compile(r"/[A-Za-z0-9_./-]{1,200}")
MAX_ONE_LINER = 120_000
"""Obergrenze für den ganzen Befehl in Byte (UTF-8). Er geht als ein Argument an `sh -c`, Linux erlaubt 128 KiB
(131 072 Byte) je Argument; der Rest bleibt Abstand."""


class SetupError(ValueError):
    """Ein Wert taugt nicht für den Einrichtungsbefehl. Der Text nennt den Wert nie."""


def _check_user(user: str) -> str:
    if not isinstance(user, str) or not USER_RE.fullmatch(user):
        raise SetupError(_USER_ERROR)
    return user


def _check_group(group: str) -> str:
    if not isinstance(group, str) or not GROUP_RE.fullmatch(group):
        raise SetupError(_GROUP_ERROR)
    return group


def _check_public_key(public_key: str) -> str:
    if not isinstance(public_key, str) or not _PUBLIC_KEY_RE.fullmatch(public_key):
        raise SetupError(_KEY_ERROR)
    return public_key


def _dq(value: str) -> str:
    """Zweite Sicherung für einen Wert, der in doppelte Anführungszeichen kommt: alles
    außer sichtbarem ASCII und jedes Zeichen, das die Shell dort deuten würde, wird
    abgelehnt (statt maskiert) -- die Werte sind vorher schon streng geprüft."""
    if any(ord(c) < 32 or ord(c) > 126 or c in _FORBIDDEN_IN_DOUBLE_QUOTES for c in value):
        raise SetupError(_KEY_ERROR)
    return value


def key_comment(host_name: str) -> str:
    """Kommentar des erzeugten Schlüssels: `nodvard@<Kurzname>`, auf sichere Zeichen reduziert (entdeckte Server können
    beliebige Namen tragen). Alte Einträge mit `lattice@<Kurzname>` erkennt der Befehl trotzdem, weil er nur nach Art und
    Schlüsseltext sucht (`build_setup_script`)."""
    return "nodvard@" + re.sub(r"[^A-Za-z0-9_.-]", "-", host_name)[:64]


@dataclass(frozen=True)
class PublicKeyInfo:
    public_key: str
    """`<Art> <Schlüssel> <Kommentar>` in einer Zeile."""
    fingerprint: str
    """`SHA256:...` wie bei `ssh-keygen -l`."""


def derive_public_key(private_key: str, *, comment: str) -> PublicKeyInfo:
    """Der öffentliche Schlüssel zu einem privaten. Der Kommentar ist immer `comment`: Der im privaten Schlüssel
    eingetragene (bei einem eingefügten Schlüssel frei wählbar) wird nie übernommen. Fehler nennen nie Schlüsselmaterial."""
    try:
        key = asyncssh.import_private_key(private_key)
        line = key.convert_to_public().export_public_key("openssh").decode("ascii")
        fingerprint = key.get_fingerprint()
        alg, blob = line.split()[:2]
    except Exception:  # noqa: BLE001 - egal was asyncssh wirft: nie den Text weitergeben
        raise SetupError("Der gespeicherte Schlüssel lässt sich nicht lesen.") from None
    return PublicKeyInfo(public_key=_check_public_key(f"{alg} {blob} {comment}"), fingerprint=fingerprint)


def helper_sudoers_file(ext_id: str) -> str:
    """Die sudoers-Datei, die einem Benutzer nur das Hilfsskript eines Moduls erlaubt (im Skript, `$U` = Benutzer).
    Unterstriche statt Bindestriche: Kennungen haben keinen `_`, so stößt der Name nie mit `nodvard-<Benutzer>` eines
    anderen Benutzers zusammen. sudo liest Dateien mit `.` oder `~` im Namen nicht, beides kommt nicht vor."""
    return f"/etc/sudoers.d/nodvard_{ext_id}_$U"


HELPERS_BLOCKED = (
    "FEHLER: Die Hilfsskripte lassen sich hier nicht sicher einrichten, und ohne sie gibt dieser Befehl den Modulen kein "
    "root. Darum hat er nichts geändert."
)
"""Feste Zeile der Vorprüfung im Modus nur mit den Regeln der Hilfsskripte; `evaluate_setup_run` erkennt sie daran."""


def _check_helpers(helpers: Sequence[RootHelperFile], helper_dir: str) -> None:
    if not _HELPER_DIR_RE.fullmatch(helper_dir) or "//" in helper_dir or "/./" in helper_dir or "/../" in helper_dir:
        raise SetupError("Ungültiger Ordner für die Hilfsskripte.")
    seen: set[str] = set()
    for helper in helpers:
        if (
            not _HELPER_ID_RE.fullmatch(helper.ext_id) or helper.ext_id in seen
            or not _SHA256_RE.fullmatch(helper.sha256) or not 1 <= helper.version <= 9999
        ):
            raise SetupError("Ungültiges Hilfsskript.")
        seen.add(helper.ext_id)


def _join(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} und {names[-1]}"


def _shown_name(helper: RootHelperFile) -> str:
    # Der Name landet in `echo "..."` innerhalb von `sh -c '...'`; mit einem Zeichen, das dort zählt, steht die Kennung
    # da.
    name = helper.ext_name
    return helper.ext_id if any(c in _FORBIDDEN_IN_DOUBLE_QUOTES or not c.isprintable() for c in name) else name


def _full_rights_help(helpers: Sequence[RootHelperFile]) -> str:
    """Wem „Volle Rechte“ nicht hilft, als Satz; leer, wenn alle Module ohne ihr Skript die volle Regel nehmen."""
    stuck = [_shown_name(h) for h in helpers if not h.sudo_fallback]
    if not stuck:
        return ""
    rescued = [_shown_name(h) for h in helpers if h.sudo_fallback]
    head = f"„Volle Rechte“ hilft hier nur {_join(rescued)}" if rescued else "„Volle Rechte“ hilft hier nicht"
    tail = "arbeitet nur über sein Hilfsskript" if len(stuck) == 1 else "arbeiten nur über ihre Hilfsskripte"
    return f"{head}: {_join(stuck)} {tail}."


def _helper_functions(helper_dir: str, helpers: Sequence[RootHelperFile]) -> list[str]:
    """`D` und die Funktionen für die Hilfsskripte. Ein Skript bekommt seine Regel nur, wenn es und jeder Ordner bis `/`
    root gehören und weder andere noch der Benutzer selbst dort schreiben können (`W`): Sonst könnte er das Skript tauschen
    und wäre über die Regel root."""
    rescue = _full_rights_help(helpers)
    link_help = f". {rescue}" if rescue else "; hier hilft nur „Volle Rechte“."
    return [
        f'D="{_dq(helper_dir)}"; HX=; WL=FEHLER',
        # WA: für die Meldung der erste benannte ACL-Eintrag außer root, der schreiben darf (AN).
        (
            'WA() { AN=; AL=$(getfacl -c "$1" 2>/dev/null) || return 0; for AE in $AL; do '
            'case "$AE" in user:?*:?w?|group:?*:?w?) AV=${AE#*:}; AV=${AV%:*}; '
            'case "$AV" in root|0) ;; *) AN=$AV; return 0;; esac;; esac; done; }'
        ),
        # W: kein Link, gehört root, der Benutzer kann nicht schreiben (als er selbst gefragt, so zählen auch ACLs),
        # keine Gruppe, auch nicht root, und kein anderer, sonst könnte jeder davon das Skript tauschen. Mit einer ACL
        # sind die Gruppenbits die Maske: Ohne w darin schreibt auch kein benannter Eintrag.
        (
            'W() { [ ! -L "$1" ] && [ "$(stat -c %u "$1" 2>/dev/null)" = 0 ] || return 1; '
            '! R test -w "$1" 2>/dev/null || return 1; M=$(stat -c %a "$1" 2>/dev/null) || return 1; '
            'case "$M" in ""|*[2367]|*[2367]?) return 1;; esac; return 0; }'
        ),
        'WD() { P="$1"; while :; do W "$P" || return 1; [ "$P" = / ] && return 0; P=$(dirname "$P"); done; }',
        # WM: warum W einen Ordner ablehnt, mit Abhilfe; WL ist der Anfang der Meldung. Löschen nur für den eigenen Ordner
        # vorschlagen, ein Link weiter oben kann zum System gehören. Schreibt über die Maske ein benannter
        # ACL-Eintrag, nennt die Meldung ihn statt der Gruppe.
        (
            'WM() { if [ -L "$1" ] && [ "$1" = "$D" ]; then echo "$WL: $1 ist ein Link. Die Hilfsskripte gehören in einen '
            'echten Ordner. Abhilfe als root: rm $1 (entfernt nur den Link, nicht sein Ziel)."; '
            'elif [ -L "$1" ]; then echo "$WL: $1 ist ein Link. Hilfsskripte legt Nodvard Deck nur auf einem Weg aus '
            f'echten Ordnern ab{link_help}"; '
            'elif [ ! -e "$1" ]; then echo "$WL: Den Ordner $1 gibt es nicht. Abhilfe als root: mkdir -p $1"; '
            'elif [ ! -d "$1" ]; then echo "$WL: $1 ist kein Ordner. Abhilfe als root: mv $1 $1.alt"; '
            'elif [ "$(stat -c %u "$1" 2>/dev/null)" != 0 ]; then echo "$WL: Der Ordner $1 gehört nicht root. Abhilfe '
            'als root: chown root:root $1"; '
            'elif M=$(stat -c %a "$1" 2>/dev/null) && case "$M" in *[2367]) false;; *[2367]?) true;; *) false;; esac; '
            'then WA "$1"; if [ -n "$AN" ]; then echo "$WL: Im Ordner $1 gibt eine ACL (erweiterte Rechte) $AN '
            'Schreibrecht. Darüber könnte jemand die Hilfsskripte austauschen und wäre damit root. '
            'Abhilfe als root: setfacl -b $1 && chmod g-w $1"; '
            'else WG=$(stat -c %G "$1" 2>/dev/null); [ "$WG" != UNKNOWN ] || WG="mit der Nummer '
            '$(stat -c %g "$1" 2>/dev/null)"; echo "$WL: Im Ordner $1 darf die Gruppe $WG schreiben (Rechte $M). Wer in ihr '
            'ist, könnte die Hilfsskripte austauschen und wäre damit root. Abhilfe als root: chmod g-w $1"; '
            '[ "$WG" != staff ] || echo "Hinweis: Ältere Debian lassen die Gruppe staff in /usr/local und /usr/local/lib '
            'schreiben. Abhilfe als root für beide auf einmal: chmod g-w /usr/local /usr/local/lib"; fi; '
            'else echo "$WL: Im Ordner $1 darf außer root noch jemand schreiben (Rechte $(stat -c %a "$1" 2>/dev/null), '
            'Gruppe $(stat -c %G "$1" 2>/dev/null)). Abhilfe als root: chmod go-w $1 (bei einer ACL vorher: setfacl -b $1)"; fi; }'
        ),
        (
            'HS() { Y=$(mktemp) || return 1; if printf %s "$3" | base64 -d > "$Y" 2>/dev/null && [ "$(sha256sum "$Y" | cut -d" " -f1)" = "$2" ] '
            '&& install -m 0755 -o root -g root "$Y" "$D/$1" && W "$D/$1"; then rm -f "$Y"; return 0; fi; rm -f "$Y"; return 1; }'
        ),
        (
            'HR() { Z=$(mktemp) || return 1; echo "$U ALL=(root) NOPASSWD: $D/$1" > "$Z"; '
            'if visudo -c -q -f "$Z" && install -m 0440 -o root -g root "$Z" "$2"; then rm -f "$Z"; VC "$2" && return 0; rm -f "$2"; return 1; fi; '
            'rm -f "$Z"; return 1; }'
        ),
    ]


def _helper_precheck(helpers: Sequence[RootHelperFile]) -> list[str]:
    """Vorprüfung, wenn nur die Regeln der Hilfsskripte root geben: Lassen sich die Skripte nicht sicher einrichten, endet
    der Befehl mit Grund und Abhilfe, bevor er etwas ändert."""
    rescue = _full_rights_help(helpers) or (
        "Oder in Nodvard Deck unter „Erweitert“ „Volle Rechte“ wählen, dann laufen die Module über die volle "
        "sudo-Regel."
    )
    return [
        "HB=",
        # HB=1: ohne sudo hilft auch die volle Regel nicht. Einen vorhandenen, aber falsch eingestellten eigenen Ordner lehnt
        # die Vorprüfung ab, statt ihn wie `install -d` später still zu reparieren: So ändert der Befehl nichts.
        (
            'if ! command -v visudo >/dev/null; then echo "FEHLER: sudo ist nicht installiert (als root: apt install sudo)."; HB=1; '
            'elif ! command -v base64 >/dev/null || ! command -v sha256sum >/dev/null || ! command -v stat >/dev/null; then '
            'echo "FEHLER: base64, sha256sum oder stat fehlt hier. Sie gehören zu den coreutils, bitte nachinstallieren."; HB=2; '
            'elif ! WD "$(dirname "$D")"; then WM "$P"; HB=2; '
            'elif { [ -e "$D" ] || [ -L "$D" ]; } && { [ ! -d "$D" ] || ! W "$D"; }; then WM "$D"; HB=2; fi'
        ),
        (
            f'[ -z "$HB" ] || {{ echo "{HELPERS_BLOCKED}"; [ "$HB" = 1 ] || echo "Hinweis: Den Grund oben beheben und den '
            f'Befehl noch einmal ausführen. {rescue}"; exit 1; }}'
        ),
    ]


def _skipped_with_full_rule(helpers: Sequence[RootHelperFile]) -> str:
    rescued = [_shown_name(h) for h in helpers if h.sudo_fallback]
    stuck = [_shown_name(h) for h in helpers if not h.sudo_fallback]
    if not stuck:
        return ", die Module nutzen die volle sudo-Regel"
    lost = f"{_join(stuck)} {'hat' if len(stuck) == 1 else 'haben'} hier kein root"
    if not rescued:
        return f", {lost}"
    return f", {_join(rescued)} {'nutzt' if len(rescued) == 1 else 'nutzen'} die volle sudo-Regel, {lost}"


def _helper_lines(helpers: Sequence[RootHelperFile], *, strict: bool = False) -> list[str]:
    """Hilfsskripte der Module, base64-kodiert und gegen ihre sha256 geprüft, samt je einer eigenen sudo-Regel. Ein
    Fehlschlag ist neben der vollen Regel nur ein Hinweis (das Modul nimmt dann sie), ohne sie (`strict`) oder bei einem
    Modul ohne `sudo_fallback` ein Fehler."""
    every = strict or not all(helper.sudo_fallback for helper in helpers)
    level, fail = ("FEHLER", " F=1;") if every else ("Hinweis", "")
    skipped = "" if strict else _skipped_with_full_rule(helpers)
    lines = [
        (
            'if ! command -v visudo >/dev/null; then HX=1; '
            'elif ! command -v base64 >/dev/null || ! command -v sha256sum >/dev/null || ! command -v stat >/dev/null; then '
            f'echo "{level}: base64, sha256sum oder stat fehlt hier, die Hilfsskripte wurden übersprungen.";{fail} HX=1; '
            f'elif ! WD "$(dirname "$D")"; then WL={level}; WM "$P"; HX=2; '
            f'elif [ -L "$D" ] || ! install -d -m 0755 -o root -g root "$D" || ! W "$D"; then WL={level}; WM "$D"; HX=2; fi; '
            f'[ "$HX" != 2 ] || {{ echo "{level}: Deshalb wurden die Hilfsskripte übersprungen{skipped}.";{fail} }}'
        ),
    ]
    for helper in sorted(helpers, key=lambda h: h.ext_id):
        name = f"{helper.ext_id}-root"
        blob = base64.b64encode(helper.text.encode("ascii")).decode("ascii")
        own, own_fail = ("FEHLER", " F=1;") if strict or not helper.sudo_fallback else ("Hinweis", "")
        lines.append(
            f'if [ -z "$HX" ]; then if ! HS {name} {helper.sha256} "{blob}"; '
            f'then echo "{own}: Das Hilfsskript {name} wurde nicht eingerichtet: Die Datei ließ sich nicht prüfen oder nicht sicher ablegen.";{own_fail} '
            f'elif ! HR {name} "{helper_sudoers_file(helper.ext_id)}"; '
            f'then echo "{own}: Das Hilfsskript {name} wurde nicht eingerichtet: Seine sudo-Regel hat die Prüfung mit visudo nicht bestanden.";{own_fail} '
            f'else echo "Hilfsskript {name} (Version {helper.version}) eingerichtet, mit eigener sudo-Regel."; fi; fi'
        )
    return lines


FULL_RULE_REMOVED = "Die volle sudo-Regel {name} wurde entfernt."
"""Zeile je entfernter Datei (`nodvard-<Benutzer>` oder `lattice-<Benutzer>`); `setup_report` liest sie."""


def _drop_full_rule_lines(helpers: Sequence[RootHelperFile]) -> list[str]:
    """Entfernt die volle Regel von Nodvard Deck und prüft danach jedes Hilfsskript über seine eigene Regel; scheitert
    das oder bricht der Lauf ab, kommt sie zurück. Nur Dateien, die Byte für Byte die eigene Regel sind, keine Links."""
    probes = " ".join(f"{h.ext_id}-root:{h.version}" for h in sorted(helpers, key=lambda h: h.ext_id))
    return [
        # XA: die Skripte, die nicht mit ihrer Version antworten, für die Meldung.
        (
            f'XA() {{ XB=; for XV in {probes}; do '
            '[ "$(R sudo -n "$D/${XV%:*}" version 2>/dev/null)" = "${XV#*:}" ] || XB="$XB ${XV%:*}"; done; '
            '[ -z "$XB" ]; }'
        ),
        # XU: legt die gelöschten Regeln wieder an, solange der Lauf nicht bestätigt ist (XP), auch bei einem Abbruch.
        (
            'XU() { [ -n "$XP" ] || return 0; XF=; for XN in $XR; do '
            'install -m 0440 -o root -g root "$XT" "/etc/sudoers.d/$XN" || XF=1; done; XP=; }'
        ),
        (
            'XD() { XT=$(mktemp) || { echo "FEHLER: Es ließ sich keine Probedatei anlegen, die volle sudo-Regel bleibt '
            'stehen."; F=1; return 0; }; '
            'trap "XU; rm -f \\"\\$XT\\"" EXIT; trap "exit 1" HUP INT TERM; '
            'echo "$U ALL=(root) NOPASSWD: ALL" > "$XT"; XC=; XK=; XR=; XP=; '
            'if ! command -v cmp >/dev/null 2>&1; then echo "Hinweis: cmp fehlt hier (Paket diffutils), darum bleibt '
            'die volle sudo-Regel stehen. diffutils installieren und noch einmal einrichten."; return 0; fi; '
            'for XN in "nodvard-$U" "lattice-$U"; do X="/etc/sudoers.d/$XN"; '
            'if [ -L "$X" ]; then XK=1; echo "Hinweis: $X ist ein Link und bleibt stehen. Bitte selbst prüfen, ob die '
            'Regel noch gebraucht wird."; '
            'elif [ -e "$X" ]; then if cmp -s "$XT" "$X"; then XC="$XC $XN"; '
            'else XK=1; echo "Hinweis: $X ist eine andere Regel als die von Nodvard Deck und bleibt stehen. Bitte '
            'selbst prüfen, ob sie noch gebraucht wird."; fi; fi; done; '
            'if [ -z "$XC" ]; then [ -n "$XK" ] || echo "Hinweis: Eine volle sudo-Regel von Nodvard Deck gibt es hier '
            'nicht, es war nichts zu entfernen."; return 0; fi; '
            'if ! XA; then echo "FEHLER: Die volle sudo-Regel bleibt stehen, weil diese Hilfsskripte über sudo nicht '
            'antworten:$XB. Was fehlt, zeigt „Verbindung prüfen“."; F=1; return 0; fi; '
            'XE=; for XN in $XC; do XR="$XR $XN"; XP=1; '
            'rm -f "/etc/sudoers.d/$XN" || { XE="sich $XN nicht löschen ließ"; break; }; done; '
            '[ -n "$XE" ] || VG || XE="visudo danach einen Fehler meldet"; '
            '[ -n "$XE" ] || XA || XE="ohne sie diese Hilfsskripte über sudo nicht antworten:$XB"; '
            'if [ -n "$XE" ]; then XU; '
            'if [ -z "$XF" ]; then echo "FEHLER: Die volle sudo-Regel ist wieder da, weil $XE. Was fehlt, zeigt '
            '„Verbindung prüfen“."; '
            'else echo "FEHLER: Die volle sudo-Regel ließ sich nicht wiederherstellen. Richte den Server mit „Volle '
            'Rechte“ noch einmal ein."; fi; F=1; return 0; fi; '
            f'XP=; for XN in $XR; do echo "{FULL_RULE_REMOVED.format(name="$XN")}"; done; }}'
        ),
        'if [ -z "$F" ]; then XD; fi',
        # Raspberry Pi OS (010_pi-nopasswd) oder die Gruppe sudo mit NOPASSWD: sudo nennt die Datei nicht, und mit ihr
        # hätte die Prüfung oben auch ohne die eigenen Regeln geklappt.
        (
            'if [ -z "$F" ] && R sudo -n -l 2>/dev/null | grep -Eq "NOPASSWD:([[:space:]]*[A-Z_]+:)*[[:space:]]*ALL'
            '[[:space:]]*\\$"; then echo "Hinweis: $U darf weiter alles per sudo ohne Passwort, über eine Regel, die '
            'nicht von Nodvard Deck stammt (etwa 010_pi-nopasswd auf Raspberry Pi OS oder die Gruppe sudo). Sie bleibt '
            'stehen, und solange sie gilt, zeigt die Prüfung nicht, ob die Hilfsskripte allein reichen."; fi'
        ),
    ]


# VO: höchstens fünf Zeilen von `visudo -c`. VC prüft eine eigene Datei; die ganze Konfiguration (VG) nur, wenn sie vor
# der Einrichtung schon in Ordnung war (VP).
_VISUDO_FUNCTIONS = (
    'VO() { visudo -c 2>&1 | grep -v "parsed OK" | head -n 5 | sed "s/^/visudo: /"; }; '
    'VG() { [ -z "$VP" ] || visudo -c -q; }; VC() { visudo -c -q -f "$1" && VG; }'
)
_VISUDO_PRE_ERROR = (
    'echo "Hinweis: visudo meldet schon vor der Einrichtung einen Fehler, der nicht von Nodvard Deck kommt:"; VO; '
    'echo "Hinweis: Die Regeln von Nodvard Deck werden deshalb einzeln geprüft. Steht oben „bad permissions“, hilft für '
    'die genannte Datei: chmod 0440 und chown root:root.";'
)


def build_setup_script(
    user: str, public_key: str, *, groups: Iterable[str] = (), sudo: bool = False,
    helpers: Sequence[RootHelperFile] = (), helper_dir: str = ROOT_HELPER_DIR, report_state: bool = False,
    full_sudo: bool = True, drop_full_rule: bool = False,
) -> tuple[str, str]:
    """`(skript, einzeiler)`: das Skript zum Lesen und dasselbe als Argument für `sh -c`. Gruppen, sudo und Hilfsskripte
    gelten nur ohne root; `full_sudo=False` heißt nur die Regeln der Hilfsskripte, `drop_full_rule` nimmt dazu die volle
    zurück, `report_state` meldet den Stand der Dateien für `setup_report`. Ungültiges ergibt `SetupError`."""
    user = _check_user(user)
    key = _check_public_key(public_key)
    wanted: list[str] = []
    for group in groups:
        group = _check_group(group)
        if group not in wanted:
            wanted.append(group)
    is_root = user == "root"
    narrow = sudo and not is_root and not full_sudo
    if narrow and not helpers:
        raise SetupError("Ohne Hilfsskripte gibt es keine engere sudo-Regel.")
    if drop_full_rule and not narrow:
        raise SetupError("Die volle sudo-Regel lässt sich nur zusammen mit den Regeln der Hilfsskripte entfernen.")
    with_helpers = sudo and not is_root and bool(helpers)
    if with_helpers:
        _check_helpers(helpers, helper_dir)

    # `B` = Art und Schlüsseltext ohne Kommentar: Ein schon eingetragener Schlüssel wird auch dann erkannt, wenn er mit
    # einem anderen Kommentar (z. B. `lattice@...`) auf dem Server steht.
    lines = ["set -e", f"U={shlex.quote(user)}", f'K="{_dq(key)}"', 'B=$(echo "$K" | cut -d" " -f1,2)']
    if not is_root:
        lines.append(
            'R() { if command -v runuser >/dev/null 2>&1; then runuser -u "$U" -- "$@"; '
            'else su -s /bin/sh "$U" -c "exec \\"\\$@\\"" -- sh "$@"; fi; }'
        )
    if with_helpers:
        lines += _helper_functions(helper_dir, helpers)
    if narrow:
        lines += _helper_precheck(helpers)
    if report_state:
        lines += [_STATE_FUNCTION, f'for f in {" ".join(SETUP_ACCOUNT_FILES)}; do ST before "$f"; done']
    if not is_root:
        # "*" statt "!": sshd mit `UsePAM no` behandelt ein mit "!" gesperrtes Konto als gesperrt.
        # Q: Befehl ohne die Warnungen „configuration error - unknown item“. useradd und usermod geben sie aus, wenn
        # /etc/login.defs Einträge enthält, die sie nicht kennen (auf Raspberry Pi OS gesehen). Nodvard Deck schreibt dort
        # nichts; alle anderen Meldungen und der Rückgabewert bleiben.
        lines.append(
            'Q() { E=$("$@" 2>&1) && C=0 || C=$?; [ -z "$E" ] || printf "%s\\n" "$E" | grep -v "configuration error - unknown item" || true; return $C; }'
        )
        lines.append(
            'id "$U" >/dev/null 2>&1 || { Q useradd -m -s /bin/bash -c "Nodvard Deck" "$U"; Q usermod -p "*" "$U"; '
            'echo "Benutzer $U angelegt."; }'
        )
    lines += [
        'H=$(getent passwd "$U" | cut -d: -f6); G=$(id -gn "$U")',
        '[ -d "$H" ] || { echo "FEHLER: Das Home-Verzeichnis $H gibt es nicht."; exit 1; }',
    ]
    if report_state:
        lines.append('ST before "$H/.ssh/authorized_keys"')
    lines += [_KEY_OPTIONS, _KEY_FIX_FUNCTION]
    if is_root:
        lines += [
            # root: das Home ist /root. Proxmox: authorized_keys ist ein Link ins Cluster-Dateisystem,
            # dort scheitert chmod -- der Link bleibt unberührt.
            'install -d -m 700 -o "$U" -g "$G" "$H/.ssh"; A="$H/.ssh/authorized_keys"; [ -e "$A" ] || touch "$A"',
            # Fehlt das Zeilenende der letzten Zeile, würde der neue Eintrag an sie gehängt.
            '[ ! -s "$A" ] || [ -z "$(tail -c1 "$A")" ] || echo >> "$A"',
            # `$KO`: kein Weiterleiten, kein Agent; ein Terminal (Terminal, `docker logs -f`), Befehle und SFTP gehen weiter.
            f'{_key_present("")} || {{ echo "$KO $K" >> "$A"; echo "{KEY_ADDED}"; }}',
            _key_restrict(""),
            _key_fix(""),
            '[ -L "$A" ] || { chown "$U:$G" "$A"; chmod 600 "$A"; }',
        ]
    else:
        # Alles an ~/.ssh und authorized_keys läuft als der Benutzer (R): So kann ein Link dort (auf /root/.ssh, /etc,
        # eine sudoers-Datei) dem Benutzer keine Rechte an fremden Dateien verschaffen, weder per chown noch durch
        # Anhängen als root. Zusätzlich bricht das Skript bei einem Link ab, statt ihm zu folgen.
        lines += [
            'S="$H/.ssh"; A="$S/authorized_keys"',
            '[ ! -L "$S" ] || { echo "FEHLER: $S ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            '[ ! -L "$A" ] || { echo "FEHLER: $A ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            'R mkdir -p -m 700 "$S"; R chmod 700 "$S"',
            # Fehlt das Zeilenende der letzten Zeile, würde der neue Eintrag an sie gehängt.
            '[ -z "$(R tail -c1 "$A" 2>/dev/null)" ] || printf "\\n" | R tee -a "$A" >/dev/null',
            f'{_key_present("R ")} || {{ printf "%s\\n" "$KO $K" | R tee -a "$A" >/dev/null; echo "{KEY_ADDED}"; }}',
            _key_restrict("R "),
            _key_fix("R "),
            'R chmod 600 "$A"',
        ]
    if not is_root:
        for group in wanted:
            g = shlex.quote(group)
            lines.append(
                f'if getent group {g} >/dev/null; then Q usermod -aG {g} "$U"; echo "Zur Gruppe {group} hinzugefügt."; '
                f'else echo "Hinweis: Gruppe {group} gibt es hier nicht – übersprungen."; fi'
            )
        if narrow:
            # Nur die Regeln der Hilfsskripte: Eine vorhandene volle Regel (nodvard- oder lattice-<Benutzer>) nimmt nur
            # `drop_full_rule` zurück, nach allen Regeln der Skripte.
            # Dass visudo da ist, hat die Vorprüfung schon festgestellt.
            lines.append(_VISUDO_FUNCTIONS)
            lines.append(f'VP=; visudo -c -q >/dev/null 2>&1 && VP=1; [ -n "$VP" ] || {{ {_VISUDO_PRE_ERROR} }}')
        elif sudo:
            # Neue Regel: /etc/sudoers.d/nodvard-<Benutzer>. Eine alte Regel lattice-<Benutzer> wird erst nach
            # erfolgreicher Prüfung der neuen entfernt, und nur, wenn sie Byte für Byte die Regel ist, die dieser Befehl
            # selbst angelegt hat (`cmp` gegen die neue Datei); alles andere bleibt stehen (Hinweis).
            # Geprüft wird jede eigene Datei für sich (VC). Die ganze Konfiguration (`visudo -c`) muss danach nur dann
            # gelten, wenn sie es vorher schon tat (VP): Eine fremde Datei mit falschen Rechten (z. B. Modus 0644) lässt
            # `visudo -c` scheitern, obwohl sudo läuft und die Regel von Nodvard Deck in Ordnung ist. visudo nennt die
            # Datei dann im Hinweis (VO).
            lines.append(_VISUDO_FUNCTIONS)
            lines.append(
                'if command -v visudo >/dev/null; then T=$(mktemp); trap "rm -f \\"\\$T\\"" EXIT; N="/etc/sudoers.d/nodvard-$U"; O="/etc/sudoers.d/lattice-$U"; '
                'VP=; visudo -c -q >/dev/null 2>&1 && VP=1; '
                f'[ -n "$VP" ] || {{ {_VISUDO_PRE_ERROR} }}; '
                'echo "$U ALL=(root) NOPASSWD: ALL" > "$T"; '
                'if visudo -c -q -f "$T"; then install -m 0440 -o root -g root "$T" "$N"; '
                'if VC "$N"; then '
                'if [ -L "$O" ]; then echo "Hinweis: $O ist ein Link und bleibt stehen. Bitte selbst prüfen, ob die Regel noch gebraucht wird."; '
                'elif [ -e "$O" ]; then '
                'if cmp -s "$T" "$O"; then rm -f "$O"; echo "Die alte sudo-Regel lattice-$U wurde durch nodvard-$U ersetzt."; '
                'VG || { install -m 0440 -o root -g root "$T" "$O"; echo "FEHLER: Die alte sudo-Regel wurde wiederhergestellt."; F=1; }; '
                'else echo "Hinweis: $O ist eine andere Regel als die von Nodvard Deck und bleibt stehen. Bitte selbst prüfen, ob sie noch gebraucht wird."; fi; '
                'fi; '
                'else echo "FEHLER: sudo-Regel zurückgenommen. visudo meldet:"; VO; rm -f "$N"; F=1; fi; '
                'else echo "FEHLER: visudo hat die sudo-Regel abgelehnt, sie wurde nicht eingetragen."; F=1; fi; '
                'else echo "Hinweis: sudo ist nicht installiert (als root: apt install sudo)."; fi'
            )
    if with_helpers:
        lines += _helper_lines(helpers, strict=narrow)
    if drop_full_rule:
        lines += _drop_full_rule_lines(helpers)
    if report_state:
        lines.append(f'for f in {" ".join(SETUP_ACCOUNT_FILES)} "$H/.ssh/authorized_keys"; do ST after "$f"; done')
    if sudo and not is_root:
        lines.append('[ -z "$F" ] || { echo "Nicht alles hat geklappt – bitte die Meldungen oben lesen."; exit 1; }')
    lines.append('echo "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken."')

    script = "\n".join(lines)
    one_liner = f'S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c {shlex.quote("; ".join(lines))}'
    if len(one_liner.encode("utf-8")) > MAX_ONE_LINER:
        raise SetupError("Der Einrichtungsbefehl wäre zu lang.")
    return script, one_liner


def _blocked_advice(no_fallback: Sequence[str]) -> str:
    if not no_fallback:
        return (
            "Dann den Grund beheben oder unter „Erweitert“ „Volle Rechte“ ankreuzen und den Befehl noch einmal "
            "ausführen."
        )
    who = "das Modul arbeitet nur über dieses Skript" if len(no_fallback) == 1 else (
        "die Module arbeiten nur über diese Skripte"
    )
    return (
        "Dann den Grund beheben und den Befehl noch einmal ausführen. „Volle Rechte“ hilft bei "
        f"{_join(no_fallback)} nicht, {who}."
    )


def _no_fallback_note(no_fallback: Sequence[str]) -> str:
    if not no_fallback:
        return ""
    if len(no_fallback) == 1:
        return (
            f" Ausnahme: {no_fallback[0]}. Ohne dieses Skript hat sein Modul hier kein root, auch nicht über die "
            "volle Regel."
        )
    return (
        f" Ausnahmen: {_join(no_fallback)}. Ohne diese Skripte haben ihre Module hier kein root, auch nicht über "
        "die volle Regel."
    )


def setup_notes(
    user: str, *, groups: Iterable[str], sudo: bool, helpers: Sequence[str] = (), full_sudo: bool = True,
    no_fallback: Sequence[str] = (), drop_full_rule: bool = False,
) -> list[str]:
    """Hinweise zum Befehl, in einfachem Deutsch. `helpers`: Namen der Hilfsskripte, die der Befehl mitbringt, davon
    `no_fallback` die, ohne die ihr Modul auch mit voller Regel kein root hat; `full_sudo=False`: keine volle Regel,
    `drop_full_rule`: eine vorhandene wird entfernt."""
    notes = [(
        "Der Schlüssel gilt nur zum Anmelden und für Befehle: kein Weiterleiten von Ports, kein Agent. Terminal, Dateien "
        "und Live-Protokolle funktionieren weiter. Auf Servern mit Dropbear (etwa DietPi) trägt Nodvard Deck ihn so "
        "ein, wie Dropbear ihn versteht; klappt die Anmeldung dort nach einer älteren Einrichtung nicht, einfach noch "
        "einmal einrichten."
    )]
    notes.append(
        "Ersetzt du später diesen Zugang durch einen neuen Schlüssel: erst mit dem neuen „Verbindung prüfen“. "
        "Der alte öffentliche Schlüssel bleibt in ~/.ssh/authorized_keys auf dem Server stehen – "
        "dort bei Bedarf selbst entfernen."
    )
    if user == "root":
        notes.append(
            "Ist der Server Teil eines Clusters mit gemeinsamer Schlüsseldatei (bei Virtualisierungs-Clustern "
            "üblich), gilt der Schlüssel für alle Knoten."
        )
        return notes
    groups = list(groups)
    full = sudo and full_sudo
    if groups or full:
        who = []
        if groups:
            who.append(f"in der {'Gruppe' if len(groups) == 1 else 'Gruppen'} {', '.join(groups)} ist")
        if full:
            who.append("sudo ohne Passwort darf")
        notes.append(f"Wer {' oder '.join(who)}, kann auf dem Server praktisch alles.")
    if sudo and not full_sudo:
        notes.append(
            f"Der Befehl gibt dem Benutzer kein volles root. Er legt die Hilfsskripte {', '.join(helpers)} nach "
            "/usr/local/lib/nodvard-deck und erlaubt per sudo nur diese Skripte, jedes mit einer eigenen Regel. Was sie "
            "dürfen, steht in ihnen selbst (etwa Updates einspielen oder Dienste neu starten); das sind weiterhin "
            "Rechte von root, aber nur für diese Aufgaben."
        )
        notes.append(
            "Lassen sich die Skripte nicht sicher ablegen (etwa weil ein Ordner auf dem Weg für andere beschreibbar ist), "
            "ändert der Befehl nichts und nennt Grund und Abhilfe; ohne volle sudo-Regel hätte das Modul sonst auf "
            f"diesem Server gar kein root. {_blocked_advice(no_fallback)}"
        )
        if drop_full_rule:
            notes.append(
                "Eine volle sudo-Regel, die Nodvard Deck selbst angelegt hat, entfernt der Befehl und fragt danach "
                "jedes Hilfsskript über seine eigene Regel. Antwortet eins nicht oder bricht der Lauf ab, stellt er "
                "sie sofort wieder her. Regeln mit anderem Inhalt und Links bleiben stehen. Danach gehen eigene "
                "Befehle mit sudo über Nodvard Deck nicht mehr, und bringt ein Update neue Hilfsskripte, haben die "
                "Module dort erst nach dem nächsten Einrichten wieder root."
            )
        else:
            notes.append(
                "Eine schon vorhandene volle sudo-Regel nimmt der Befehl nicht zurück. Das geht unter „Erweitert“ mit "
                "„Volle sudo-Regel entfernen“."
            )
    elif sudo:
        notes.append(
            "Nodvard Deck startet einige root-Befehle über „sudo sh -c“, darum braucht es die volle sudo-Regel. "
            "Deshalb bekommt nur dieser eine Benutzer die Rechte, und er kann sich nur mit dem Schlüssel anmelden."
        )
    if sudo and full_sudo and helpers:
        notes.append(
            f"Der Befehl legt außerdem die Hilfsskripte {', '.join(helpers)} nach /usr/local/lib/nodvard-deck "
            "und erlaubt jedes mit einer eigenen sudo-Regel nur für sich. Die Module rufen ein Skript nur auf, wenn "
            "es auf dem Server genau das mitgelieferte ist; sonst nehmen sie die volle Regel."
            f"{_no_fallback_note(no_fallback)}"
        )
    if groups:
        notes.append(
            "Neue Gruppen gelten erst bei einer neuen Anmeldung – „Verbindung prüfen“ sorgt dafür, "
            "dass Nodvard Deck danach neu verbindet."
        )
    return notes


# ---------------------------------------------------------------------------
# Das Dashboard führt den Befehl selbst aus (Einrichten mit Passwort)
# ---------------------------------------------------------------------------

SETUP_STARTED = "@@nodvard-setup-started"
"""Marke, die die Shell vor dem Skript ausgibt: Fehlt sie in der Ausgabe, ist schon `sudo` (oder der
Start der Shell) gescheitert, nicht das Skript."""
SETUP_INPUT_READ = "@@nodvard-setup-read"
"""Marke, die die Login-Shell bei einem langen Befehl ausgibt, wenn sie das Skript ganz gelesen hat."""

_MESSAGE_PREFIXES = (
    "Benutzer ", "Zur Gruppe ", "Hinweis:", "FEHLER:", "Die alte sudo-Regel", "Die volle sudo-Regel ",
    "Nicht alles hat geklappt", "Hilfsskript ",
    KEY_ADDED,
    KEY_FIXED,
    KEY_RESTRICTED,
    "visudo: ",
)
"""Zeilen, die das Skript selbst ausgibt (`echo` in `build_setup_script`). Nur sie gehen an die Oberfläche;
`Fertig. ...` nicht, denn danach prüft das Dashboard die Verbindung selbst. `visudo: ` sind höchstens fünf Zeilen der
Prüfung `visudo -c`, damit man sieht, welche Datei auf dem Server das Problem ist."""
_MAX_MESSAGES = 20
_MAX_MESSAGE_LENGTH = 300


def remote_setup_command(script: str, *, as_root: bool, sudo_with_password: bool) -> tuple[str, bytes]:
    """Befehl und Eingabe, mit denen das Dashboard das Skript aus `build_setup_script` unverändert selbst ausführt: als
    root direkt mit `sh -c`, sonst über `sudo -n` oder, mit `sudo_with_password`, über `sudo -S`, das die Zeile hinter
    der Eingabe als Passwort liest."""
    # Das Passwort steht nie in diesem Befehl: Die Befehlszeile ist auf dem Server für alle sichtbar (`ps`) und steht in
    # asyncssh-Fehlern. Vor dem Skript stehen nur `exec </dev/null` (braucht sudo das Passwort nicht, läge es sonst in
    # der Standardeingabe, wo das Skript es lesen könnte) und die Marke `SETUP_STARTED`. `-p ''` unterdrückt die
    # Eingabeaufforderung; `env LC_ALL=C` hält sudos Meldungen englisch, so lassen sie sich einordnen.
    inner = f"exec </dev/null; echo {SETUP_STARTED}\n{script}"
    if as_root:
        start = "sh -c"
    else:
        start = "env LC_ALL=C " + ("sudo -S -p ''" if sudo_with_password else "sudo -n") + " sh -c"
    command = f"{start} {shlex.quote(inner)}"
    if len(command.encode("utf-8")) <= ssh.INLINE_COMMAND_MAX:
        return command, b""
    # Zu lang für die Befehlszeile mancher Server: Die Login-Shell liest das Skript zeilenweise zwischen zwei Marken.
    # `read` holt Byte für Byte, so bleibt die Passwortzeile dahinter ungelesen, bis sudo sie selbst liest. Die Marke
    # vorn zeigt, ob vorher schon etwas (etwa eine ~/.bashrc) von der Standardeingabe gelesen hat.
    end = ssh.end_mark()
    fail = f"{{ echo '{ssh.STDIN_INCOMPLETE}' >&2; exit {ssh.STDIN_INCOMPLETE_EXIT}; }}"
    reader = (
        f'IFS= read -r __l && [ "$__l" = {end} ] || {fail}; '
        f'__nd=; __ok=; while IFS= read -r __l; do [ "$__l" = {end} ] && {{ __ok=1; break; }}; '
        f'__nd="$__nd$__l\n"; done; '
        f'[ -n "$__ok" ] || {fail}; echo {SETUP_INPUT_READ} && exec {start} "$__nd"'
    )
    return reader, f"{end}\n{inner}\n{end}\n".encode()


def setup_messages(stdout: str) -> list[str]:
    """Die Meldungen des Skripts aus seiner Ausgabe: nur Zeilen mit festem Anfang, nur druckbare Zeichen, gekürzt.
    Alles andere (Ausgaben der Systembefehle, Fehler von sudo) bleibt draußen."""
    messages: list[str] = []
    for raw in stdout.splitlines():
        line = "".join(c for c in raw if c.isprintable()).strip()
        if line.startswith(_MESSAGE_PREFIXES) and line not in messages:
            messages.append(line[:_MAX_MESSAGE_LENGTH])
        if len(messages) >= _MAX_MESSAGES:
            break
    return messages


@dataclass(frozen=True)
class SetupRun:
    """Wie der Lauf des Skripts ausgegangen ist."""

    outcome: str
    """`ok`, `sudo_failed` (das Skript lief gar nicht erst), `helpers_blocked` (die Vorprüfung für die Hilfsskripte hat
    abgebrochen, nichts geändert) oder `setup_failed`."""
    reason: str
    """Fester deutscher Satz (leer bei `ok`)."""
    messages: list[str]
    """Meldungen des Skripts (`setup_messages`)."""


def _stdout_line(mark: str) -> Callable[[str, str], bool]:
    # Nur als eigene Zeile in stdout: eine Meldung von sudo nennt den ganzen Befehl samt Marke (auf stderr).
    return lambda stdout, _stderr: any(line.strip() == mark for line in stdout.splitlines())


def _in_stderr(*signs: str) -> Callable[[str, str], bool]:
    return lambda _stdout, stderr: any(sign in stderr.lower() for sign in signs)


def _incomplete_line(_stdout: str, stderr: str) -> bool:
    return any(line.strip() == ssh.STDIN_INCOMPLETE for line in stderr.splitlines())


SETUP_FLAGS: dict[str, Callable[[str, str], bool]] = {
    "started": _stdout_line(SETUP_STARTED),
    "input_read": _stdout_line(SETUP_INPUT_READ),
    "incomplete": _incomplete_line,
    "helpers_blocked": _stdout_line(HELPERS_BLOCKED),
    # Fehlerausgabe von sudo (englisch, `LC_ALL=C`).
    "sudo_not_allowed": _in_stderr("not in the sudoers", "may not run sudo", "is not allowed to"),
    "sudo_needs_password": _in_stderr("a password is required"),
    "sudo_wrong_password": _in_stderr("incorrect password", "try again", "no password was provided"),
    "sudo_needs_tty": _in_stderr("terminal is required", "no tty present", "requiretty"),
    "sudo_missing": _in_stderr("not found"),
}
"""Für `one_time_password.run_once(flags=SETUP_FLAGS)`: ausgewertet wird auf der unveränderten Ausgabe, bevor
das Passwort unkenntlich gemacht wird (sonst zerstörte ein Passwort wie „setup“ die Marke). Zurück kommen nur
die Namen."""


def setup_flags(stdout: str, stderr: str) -> frozenset[str]:
    """Die Namen aus `SETUP_FLAGS`, die auf diese Ausgabe zutreffen."""
    return frozenset(name for name, test in SETUP_FLAGS.items() if test(stdout, stderr))


def _sudo_reason(code: int | None, flags: frozenset[str], *, sudo_with_password: bool) -> str:
    if "sudo_not_allowed" in flags:
        return "Das Anmelde-Konto darf kein sudo. Melde dich als root an oder nimm ein Konto mit sudo-Rechten."
    if "sudo_needs_password" in flags and not sudo_with_password:
        return "sudo verlangt ein Passwort. Setze den Haken „Für sudo dasselbe Passwort nehmen“."
    if "sudo_wrong_password" in flags:
        return "sudo hat das Passwort nicht angenommen."
    if "sudo_needs_tty" in flags:
        return (
            "sudo verlangt auf diesem Server ein Terminal. Melde dich als root an oder füge den Befehl selbst "
            "auf dem Server ein."
        )
    if code == 127 or "sudo_missing" in flags:
        return "sudo ist auf dem Server nicht installiert. Melde dich als root an."
    return "sudo hat nicht geklappt. Melde dich als root an oder füge den Befehl selbst auf dem Server ein."


_INCOMPLETE_REASON = (
    "Der Einrichtungsbefehl kam nicht vollständig auf dem Server an, deshalb wurde nichts ausgeführt. "
    "Bitte versuche es noch einmal."
)
_LOGIN_SHELL_REASON = (
    "Die Login-Shell dieses Kontos konnte den langen Einrichtungsbefehl nicht lesen, deshalb wurde nichts ausgeführt. "
    "Das geht nur mit einer Shell wie sh oder bash, nicht mit fish oder tcsh. Melde dich mit einem anderen Konto an "
    "oder füge den Befehl selbst in einer sh- oder bash-Sitzung auf dem Server ein."
)


def evaluate_setup_run(
    exit_status: int | None, stdout: str, stderr: str, *, via_sudo: bool, sudo_with_password: bool,
    flags: frozenset[str] | None = None, long_command: bool = False,
) -> SetupRun:
    """Ordnet den Lauf von `remote_setup_command` ein (`long_command`: mit Eingabe); nur feste Sätze und
    `setup_messages` gehen weiter. `flags` sind die Namen aus `SETUP_FLAGS` auf der unveränderten Ausgabe
    (`RemoteResult.flags`), ohne sie werden sie aus `stdout`/`stderr` gebildet."""
    flags = setup_flags(stdout, stderr) if flags is None else flags
    started = "started" in flags
    messages = setup_messages(stdout)
    if started and exit_status == 0:
        return SetupRun("ok", "", messages)
    if not started and exit_status == ssh.STDIN_INCOMPLETE_EXIT and "incomplete" in flags:
        return SetupRun("setup_failed", _INCOMPLETE_REASON, [])
    if not started and long_command and "input_read" not in flags:
        return SetupRun("sudo_failed" if via_sudo else "setup_failed", _LOGIN_SHELL_REASON, [])
    if not started and via_sudo:
        return SetupRun("sudo_failed", _sudo_reason(exit_status, flags, sudo_with_password=sudo_with_password), [])
    if not started:
        return SetupRun("setup_failed", "Der Befehl ließ sich auf dem Server nicht starten.", [])
    if "helpers_blocked" in flags:
        return SetupRun(
            "helpers_blocked",
            (
                "Die Hilfsskripte lassen sich auf diesem Server nicht sicher einrichten, und ohne sie bekämen die Module dort "
                "kein root. Darum hat Nodvard Deck nichts geändert. Was am Server zu tun ist, steht in den Meldungen unten; "
                "danach noch einmal einrichten."
            ),
            messages,
        )
    if not messages:
        # Bricht das Skript ohne eigene Zeile ab (z. B. weil ein Systembefehl scheitert), gibt es unten nichts zu lesen.
        return SetupRun(
            "setup_failed",
            (
                "Der Einrichtungsbefehl ist auf dem Server fehlgeschlagen, hat aber keine Meldung hinterlassen. "
                "Füge den Befehl stattdessen selbst auf dem Server ein („Befehl selbst einfügen“): "
                "Dort siehst du die Fehlermeldung."
            ),
            messages,
        )
    return SetupRun(
        "setup_failed",
        "Der Einrichtungsbefehl ist auf dem Server fehlgeschlagen. Die Meldungen unten sagen, woran es lag.",
        messages,
    )


# ---------------------------------------------------------------------------
# Was der Lauf getan hat, für den Datei-Wächter von Nodvard Shield
# ---------------------------------------------------------------------------

_NAME = r"[a-z_][a-z0-9_-]{0,31}"
_STATE_LINE_RE = re.compile(
    rf"{re.escape(STATE_MARK)} (before|after) (?:([0-9a-f]{{64}}) ([0-7]{{3,4}}) ([A-Za-z0-9_][A-Za-z0-9_.-]{{0,31}})|- - -) (/.+)"
)
_KEYS_PATH_RE = re.compile(r"(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)+/\.ssh/authorized_keys")
_HELPER_DONE_RE = re.compile(r"Hilfsskript ([a-z][a-z0-9-]{1,63})-root \(Version \d{1,4}\) eingerichtet, mit eigener sudo-Regel\.")


def setup_report(stdout: str, *, user: str) -> dict[str, Any]:
    """Was der Lauf von `build_setup_script(report_state=True)` für `user` laut seiner Ausgabe getan hat: Aktionen,
    eingerichtete Hilfsskripte und der Stand der Dateien vorher und nachher. Nur feste Formen zählen, kein Servertext;
    gedacht für die unveränderte Ausgabe (`run_once(extract=...)`), weil das Unkenntlichmachen des Passworts Prüfsummen
    zerstören kann."""
    actions: set[str] = set()
    helpers: set[str] = set()
    removed: set[str] = set()
    files: dict[str, dict[str, Any]] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if line == KEY_ADDED:
            actions.add("key_added")
        elif line in (KEY_FIXED, KEY_RESTRICTED):
            actions.add("key_fixed")
        elif line == f"Benutzer {user} angelegt.":
            actions.add("user_created")
        elif re.fullmatch(rf"Zur Gruppe {_NAME} hinzugefügt\.", line):
            actions.add("groups_added")
        elif line == f"Die alte sudo-Regel lattice-{user} wurde durch nodvard-{user} ersetzt.":
            actions.add("legacy_rule_removed")
        elif line in {FULL_RULE_REMOVED.format(name=f"{kind}-{user}") for kind in ("nodvard", "lattice")}:
            actions.add("full_rule_removed")
            removed.add(line.split()[3])
        elif m := _HELPER_DONE_RE.fullmatch(line):
            helpers.add(m.group(1))
        elif (m := _STATE_LINE_RE.fullmatch(line)) and _reported_path(m.group(5)):
            state = None if m.group(2) is None else {"sha256": m.group(2), "mode": m.group(3), "owner": m.group(4)}
            files.setdefault(m.group(5), {})[m.group(1)] = state
    return {"actions": sorted(actions), "helpers": sorted(helpers), "removed_rules": sorted(removed), "files": files}


def _reported_path(path: str) -> bool:
    return path in SETUP_ACCOUNT_FILES or (_KEYS_PATH_RE.fullmatch(path) is not None and "/../" not in path and "/./" not in path)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def setup_record(
    user: str, report: dict[str, Any], *, sudo: bool, helpers: Sequence[RootHelperFile] = (),
    helper_dir: str = ROOT_HELPER_DIR, full_sudo: bool = True, drop_full_rule: bool = False,
) -> dict[str, Any]:
    """Was der Datei-Wächter als eigene Einrichtung erkennt (`Host.metadata["setup_run"]`): der Bericht und die
    Prüfsummen der sudo-Regeln, die das Skript schreibt (`sudoers`), als alte löscht (`legacy_sudoers`) oder als volle
    entfernt hat (`removed_sudoers`), berechnet aus genau dem Inhalt, den es schreibt."""
    sudoers: dict[str, str] = {}
    legacy: dict[str, str] = {}
    removed: dict[str, str] = {}
    if sudo and user != "root":
        # Wie im Skript: `echo "$U ALL=(root) NOPASSWD: ALL"` und für ein Hilfsskript `... NOPASSWD: $D/<name>`.
        full_rule = _sha256(f"{user} ALL=(root) NOPASSWD: ALL\n")
        if full_sudo:
            sudoers[f"/etc/sudoers.d/nodvard-{user}"] = full_rule
            legacy[f"/etc/sudoers.d/lattice-{user}"] = full_rule
        elif drop_full_rule:
            for name in report.get("removed_rules", []):
                if name in (f"nodvard-{user}", f"lattice-{user}"):
                    removed[f"/etc/sudoers.d/{name}"] = full_rule
        offered = {helper.ext_id for helper in helpers}
        for ext_id in report.get("helpers", []):
            if ext_id in offered:
                path = helper_sudoers_file(ext_id).replace("$U", user)
                sudoers[path] = _sha256(f"{user} ALL=(root) NOPASSWD: {helper_dir}/{ext_id}-root\n")
    return {
        "user": user, "actions": list(report.get("actions", [])), "files": dict(report.get("files", {})),
        "sudoers": sudoers, "legacy_sudoers": legacy, "removed_sudoers": removed,
    }
