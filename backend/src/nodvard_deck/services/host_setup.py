"""Einrichtungsbefehl für einen Server: Benutzer anlegen, Schlüssel eintragen, auf Wunsch Gruppen und sudo ohne Passwort.

Reine Funktionen ohne I/O; mit Passwort führt das Dashboard das Skript selbst aus (`remote_setup_command`)."""

from __future__ import annotations

import base64
import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import asyncssh
from nodvard_sdk import ROOT_HELPER_DIR

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
_HELPER_DIR_RE = re.compile(r"/[A-Za-z0-9_./-]{1,200}")
MAX_ONE_LINER = 100_000
"""Obergrenze für den ganzen Befehl. Er geht als ein Argument an `sh -c`, Linux erlaubt 128 KiB je Argument."""


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


def _helper_lines(helpers: Sequence[RootHelperFile], helper_dir: str) -> list[str]:
    """Hilfsskripte der Module samt je einer sudo-Regel nur für dieses Skript, neben der vollen Regel. Was hier
    scheitert, gibt einen Hinweis und lässt die übrige Einrichtung laufen: Ohne passendes Skript nimmt ein Modul die
    volle Regel.

    Der Text reist base64-kodiert mit und wird auf dem Server gegen seine sha256 geprüft. Die Regel kommt nur dazu, wenn
    das Skript und jeder Ordner bis `/` root gehören und weder andere noch der Benutzer selbst dort schreiben können
    (`W`): Sonst könnte er das Skript tauschen und wäre über die Regel root."""
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
    lines = [
        f'D="{_dq(helper_dir)}"; HX=',
        # W: kein Link, gehört root, der Benutzer kann nicht schreiben (als er selbst gefragt, so zählen auch ACLs), andere
        # auch nicht; schreibt die Gruppe, darf der Benutzer nicht in ihr sein.
        (
            'W() { [ ! -L "$1" ] && [ "$(stat -c %u "$1" 2>/dev/null)" = 0 ] || return 1; ! R test -w "$1" 2>/dev/null || return 1; '
            'M=$(stat -c %a "$1" 2>/dev/null) || return 1; '
            'case "$M" in *[2367]) return 1;; *[2367]?) case " $(id -Gn "$U" 2>/dev/null) " in *" $(stat -c %G "$1" 2>/dev/null) "*) return 1;; esac;; esac; return 0; }'
        ),
        'WD() { P="$1"; while :; do W "$P" || return 1; [ "$P" = / ] && return 0; P=$(dirname "$P"); done; }',
        (
            'HS() { Y=$(mktemp) || return 1; if printf %s "$3" | base64 -d > "$Y" 2>/dev/null && [ "$(sha256sum "$Y" | cut -d" " -f1)" = "$2" ] '
            '&& install -m 0755 -o root -g root "$Y" "$D/$1" && W "$D/$1"; then rm -f "$Y"; return 0; fi; rm -f "$Y"; return 1; }'
        ),
        (
            'HR() { Z=$(mktemp) || return 1; echo "$U ALL=(root) NOPASSWD: $D/$1" > "$Z"; '
            'if visudo -c -q -f "$Z" && install -m 0440 -o root -g root "$Z" "$2"; then rm -f "$Z"; visudo -c -q && return 0; rm -f "$2"; return 1; fi; '
            'rm -f "$Z"; return 1; }'
        ),
        (
            'if ! command -v visudo >/dev/null; then HX=1; '
            'elif ! command -v base64 >/dev/null || ! command -v sha256sum >/dev/null || ! command -v stat >/dev/null; then '
            'echo "Hinweis: base64, sha256sum oder stat fehlt hier, die Hilfsskripte wurden übersprungen."; HX=1; '
            'elif ! WD "$(dirname "$D")" || [ -L "$D" ] || ! install -d -m 0755 -o root -g root "$D" || ! W "$D"; then '
            'echo "Hinweis: Der Ordner $D oder einer darüber gehört nicht root oder ist für andere beschreibbar, '
            'die Hilfsskripte wurden übersprungen."; HX=1; fi'
        ),
    ]
    for helper in sorted(helpers, key=lambda h: h.ext_id):
        name = f"{helper.ext_id}-root"
        blob = base64.b64encode(helper.text.encode("ascii")).decode("ascii")
        lines.append(
            f'if [ -z "$HX" ]; then if HS {name} {helper.sha256} "{blob}" && HR {name} "{helper_sudoers_file(helper.ext_id)}"; '
            f'then echo "Hilfsskript {name} (Version {helper.version}) eingerichtet, mit eigener sudo-Regel."; '
            f'else echo "Hinweis: Das Hilfsskript {name} wurde nicht eingerichtet."; fi; fi'
        )
    return lines


def build_setup_script(
    user: str, public_key: str, *, groups: Iterable[str] = (), sudo: bool = False,
    helpers: Sequence[RootHelperFile] = (), helper_dir: str = ROOT_HELPER_DIR,
) -> tuple[str, str]:
    """`(skript, einzeiler)`: `skript` zum Lesen (eine Anweisung je Zeile), `einzeiler` zum Einfügen (als root direkt, sonst
    über `sudo`, als ein Argument für `sh -c`). `groups`, `sudo` und `helpers` gelten nur für Benutzer außer root,
    `helpers` nur zusammen mit `sudo` (`helper_dir` ändern nur Tests); ein ungültiger Wert ergibt `SetupError`."""
    user = _check_user(user)
    key = _check_public_key(public_key)
    wanted: list[str] = []
    for group in groups:
        group = _check_group(group)
        if group not in wanted:
            wanted.append(group)
    is_root = user == "root"

    # `B` = Art und Schlüsseltext ohne Kommentar: Ein schon eingetragener Schlüssel wird auch dann erkannt, wenn er mit
    # einem anderen Kommentar (z. B. `lattice@...`) auf dem Server steht.
    lines = ["set -e", f"U={shlex.quote(user)}", f'K="{_dq(key)}"', 'B=$(echo "$K" | cut -d" " -f1,2)']
    if not is_root:
        # "*" statt "!": sshd mit `UsePAM no` behandelt ein mit "!" gesperrtes Konto als gesperrt.
        lines.append(
            'id "$U" >/dev/null 2>&1 || { useradd -m -s /bin/bash -c "Nodvard Deck" "$U"; usermod -p "*" "$U"; '
            'echo "Benutzer $U angelegt."; }'
        )
    lines += [
        'H=$(getent passwd "$U" | cut -d: -f6); G=$(id -gn "$U")',
        '[ -d "$H" ] || { echo "FEHLER: Das Home-Verzeichnis $H gibt es nicht."; exit 1; }',
    ]
    if is_root:
        lines += [
            # root: das Home ist /root. Proxmox: authorized_keys ist ein Link ins Cluster-Dateisystem,
            # dort scheitert chmod -- der Link bleibt unberührt.
            'install -d -m 700 -o "$U" -g "$G" "$H/.ssh"; A="$H/.ssh/authorized_keys"; [ -e "$A" ] || touch "$A"',
            # Fehlt das Zeilenende der letzten Zeile, würde der neue Eintrag an sie gehängt.
            '[ ! -s "$A" ] || [ -z "$(tail -c1 "$A")" ] || echo >> "$A"',
            # `restrict,pty`: kein Weiterleiten, kein Agent; ein Terminal (Terminal, `docker logs -f`),
            # Befehle und SFTP gehen weiter.
            'grep -qF -- "$B" "$A" || echo "restrict,pty $K" >> "$A"',
            '[ -L "$A" ] || { chown "$U:$G" "$A"; chmod 600 "$A"; }',
        ]
    else:
        # Alles an ~/.ssh und authorized_keys läuft als der Benutzer (R): So kann ein Link dort (auf /root/.ssh, /etc,
        # eine sudoers-Datei) dem Benutzer keine Rechte an fremden Dateien verschaffen, weder per chown noch durch
        # Anhängen als root. Zusätzlich bricht das Skript bei einem Link ab, statt ihm zu folgen.
        lines += [
            'R() { if command -v runuser >/dev/null 2>&1; then runuser -u "$U" -- "$@"; '
            'else su -s /bin/sh "$U" -c "exec \\"\\$@\\"" -- sh "$@"; fi; }',
            'S="$H/.ssh"; A="$S/authorized_keys"',
            '[ ! -L "$S" ] || { echo "FEHLER: $S ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            '[ ! -L "$A" ] || { echo "FEHLER: $A ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            'R mkdir -p -m 700 "$S"; R chmod 700 "$S"',
            # Fehlt das Zeilenende der letzten Zeile, würde der neue Eintrag an sie gehängt.
            '[ -z "$(R tail -c1 "$A" 2>/dev/null)" ] || printf "\\n" | R tee -a "$A" >/dev/null',
            # `restrict,pty`: kein Weiterleiten, kein Agent; ein Terminal (Terminal, `docker logs -f`),
            # Befehle und SFTP gehen weiter.
            'R grep -qF -- "$B" "$A" 2>/dev/null || printf "%s\\n" "restrict,pty $K" | R tee -a "$A" >/dev/null',
            'R chmod 600 "$A"',
        ]
    if not is_root:
        for group in wanted:
            g = shlex.quote(group)
            lines.append(
                f'if getent group {g} >/dev/null; then usermod -aG {g} "$U"; echo "Zur Gruppe {group} hinzugefügt."; '
                f'else echo "Hinweis: Gruppe {group} gibt es hier nicht – übersprungen."; fi'
            )
        if sudo:
            # Neue Regel: /etc/sudoers.d/nodvard-<Benutzer>. Eine alte Regel lattice-<Benutzer> wird erst nach
            # erfolgreicher Prüfung der neuen entfernt, und nur, wenn sie Byte für Byte die Regel ist, die dieser Befehl
            # selbst angelegt hat (`cmp` gegen die neue Datei); alles andere bleibt stehen (Hinweis). `visudo -c -q`
            # muss am Ende gelten.
            lines.append(
                'if command -v visudo >/dev/null; then T=$(mktemp); trap "rm -f \\"\\$T\\"" EXIT; N="/etc/sudoers.d/nodvard-$U"; O="/etc/sudoers.d/lattice-$U"; '
                'echo "$U ALL=(root) NOPASSWD: ALL" > "$T"; '
                'if visudo -c -q -f "$T"; then install -m 0440 -o root -g root "$T" "$N"; '
                'if visudo -c -q; then '
                'if [ -L "$O" ]; then echo "Hinweis: $O ist ein Link und bleibt stehen. Bitte selbst prüfen, ob die Regel noch gebraucht wird."; '
                'elif [ -e "$O" ]; then '
                'if cmp -s "$T" "$O"; then rm -f "$O"; echo "Die alte sudo-Regel lattice-$U wurde durch nodvard-$U ersetzt."; '
                'visudo -c -q || { install -m 0440 -o root -g root "$T" "$O"; echo "FEHLER: Die alte sudo-Regel wurde wiederhergestellt."; F=1; }; '
                'else echo "Hinweis: $O ist eine andere Regel als die von Nodvard Deck und bleibt stehen. Bitte selbst prüfen, ob sie noch gebraucht wird."; fi; '
                'fi; '
                'else rm -f "$N"; echo "FEHLER: sudo-Regel zurückgenommen."; F=1; fi; '
                'else echo "FEHLER: visudo hat die sudo-Regel abgelehnt, sie wurde nicht eingetragen."; F=1; fi; '
                'else echo "Hinweis: sudo ist nicht installiert (als root: apt install sudo)."; fi'
            )
    if sudo and not is_root and helpers:
        lines += _helper_lines(helpers, helper_dir)
    if sudo and not is_root:
        lines.append('[ -z "$F" ] || { echo "Nicht alles hat geklappt – bitte die Meldungen oben lesen."; exit 1; }')
    lines.append('echo "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken."')

    script = "\n".join(lines)
    one_liner = f'S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c {shlex.quote("; ".join(lines))}'
    if len(one_liner) > MAX_ONE_LINER:
        raise SetupError("Der Einrichtungsbefehl wäre zu lang.")
    return script, one_liner


def setup_notes(user: str, *, groups: Iterable[str], sudo: bool, helpers: Sequence[str] = ()) -> list[str]:
    """Hinweise zum Befehl, in einfachem Deutsch. `helpers`: Namen der Hilfsskripte, die der Befehl mitbringt."""
    notes = [
        "Der Schlüssel gilt nur zum Anmelden und für Befehle (restrict,pty): kein Weiterleiten von "
        "Ports, kein Agent. Terminal, Dateien und Live-Protokolle funktionieren weiter."
    ]
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
    if groups or sudo:
        who = []
        if groups:
            who.append(f"in der {'Gruppe' if len(groups) == 1 else 'Gruppen'} {', '.join(groups)} ist")
        if sudo:
            who.append("sudo ohne Passwort darf")
        notes.append(f"Wer {' oder '.join(who)}, kann auf dem Server praktisch alles.")
    if sudo:
        notes.append(
            "Eine enger begrenzte sudo-Regel allein reicht Nodvard Deck noch nicht: Einige root-Befehle startet es "
            "weiter über „sudo sh -c“. Darum bekommt nur dieser eine Benutzer die Rechte, und er kann sich "
            "nur mit dem Schlüssel anmelden."
        )
    if sudo and helpers:
        notes.append(
            f"Der Befehl legt außerdem die Hilfsskripte {', '.join(helpers)} nach /usr/local/lib/nodvard-deck "
            "und erlaubt jedes mit einer eigenen sudo-Regel nur für sich. Die Module nutzen sie schon, solange das "
            "Skript auf dem Server genau das mitgelieferte ist; sonst nehmen sie die volle Regel. Später reichen die "
            "Skripte statt der vollen Regel."
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

_MESSAGE_PREFIXES = (
    "Benutzer ", "Zur Gruppe ", "Hinweis:", "FEHLER:", "Die alte sudo-Regel", "Nicht alles hat geklappt", "Hilfsskript ",
)
"""Zeilen, die das Skript selbst ausgibt (`echo` in `build_setup_script`). Nur sie gehen an die Oberfläche;
`Fertig. ...` nicht, denn danach prüft das Dashboard die Verbindung selbst."""
_MAX_MESSAGES = 20
_MAX_MESSAGE_LENGTH = 300


def remote_setup_command(script: str, *, as_root: bool, sudo_with_password: bool) -> str:
    """Der Befehl, mit dem das Dashboard das Skript aus `build_setup_script` unverändert selbst ausführt: als root direkt
    mit `sh -c`, sonst über sudo (mit `sudo_with_password` liest `sudo -S` das Passwort von der Standardeingabe, sonst
    `sudo -n`)."""
    # Das Passwort steht nie in diesem Befehl: Die Befehlszeile ist auf dem Server für alle sichtbar (`ps`) und steht in
    # asyncssh-Fehlern. Vor dem Skript stehen nur `exec </dev/null` (braucht sudo das Passwort nicht, läge es sonst in
    # der Standardeingabe, wo das Skript es lesen könnte) und die Marke `SETUP_STARTED`. `-p ''` unterdrückt die
    # Eingabeaufforderung; `env LC_ALL=C` hält sudos Meldungen englisch, so lassen sie sich einordnen.
    inner = f"exec </dev/null; echo {SETUP_STARTED}\n{script}"
    if as_root:
        return f"sh -c {shlex.quote(inner)}"
    sudo = "sudo -S -p ''" if sudo_with_password else "sudo -n"
    return f"env LC_ALL=C {sudo} sh -c {shlex.quote(inner)}"


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
    """`ok`, `sudo_failed` (das Skript lief gar nicht erst) oder `setup_failed`."""
    reason: str
    """Fester deutscher Satz (leer bei `ok`)."""
    messages: list[str]
    """Meldungen des Skripts (`setup_messages`)."""


def _marker_line(stdout: str, _stderr: str) -> bool:
    # Nur als eigene Zeile in stdout: eine Meldung von sudo nennt den ganzen Befehl samt Marke (auf stderr).
    return any(line.strip() == SETUP_STARTED for line in stdout.splitlines())


def _in_stderr(*signs: str) -> Callable[[str, str], bool]:
    return lambda _stdout, stderr: any(sign in stderr.lower() for sign in signs)


SETUP_FLAGS: dict[str, Callable[[str, str], bool]] = {
    "started": _marker_line,
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


def evaluate_setup_run(
    exit_status: int | None, stdout: str, stderr: str, *, via_sudo: bool, sudo_with_password: bool,
    flags: frozenset[str] | None = None,
) -> SetupRun:
    """Ordnet den Lauf von `remote_setup_command` ein; die Ausgabe des Servers wird nie weitergereicht, nur feste Sätze
    und `setup_messages`. `flags` sind die Namen aus `SETUP_FLAGS`, ermittelt auf der unveränderten Ausgabe
    (`RemoteResult.flags`); ohne sie werden sie aus `stdout`/`stderr` gebildet."""
    flags = setup_flags(stdout, stderr) if flags is None else flags
    started = "started" in flags
    messages = setup_messages(stdout)
    if started and exit_status == 0:
        return SetupRun("ok", "", messages)
    if not started and via_sudo:
        return SetupRun("sudo_failed", _sudo_reason(exit_status, flags, sudo_with_password=sudo_with_password), [])
    if not started:
        return SetupRun("setup_failed", "Der Befehl ließ sich auf dem Server nicht starten.", [])
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
