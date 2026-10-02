"""Einrichtungsbefehl fuer einen Server: legt (falls noetig) den Benutzer an, traegt den
oeffentlichen Schluessel ein und gibt auf Wunsch Gruppen und sudo ohne Passwort.

Reine Funktionen, kein I/O. Der Befehl wird vom Menschen auf dem Server ausgefuehrt --
deshalb gilt: **nichts Ungeprueftes kommt in das Skript.** Benutzer, Gruppen und
oeffentlicher Schluessel werden streng per Zeichenliste geprueft (Fehlermeldungen nennen den
Wert nie), ein Wert mit Anfuehrungszeichen, `$`, Backtick, Backslash oder Zeilenumbruch kommt
gar nicht erst durch (`_dq`), und das ganze Skript wird fuer `sh -c` mit `shlex.quote`
verpackt. Das Skript selbst enthaelt kein einfaches Hochkommas -- es liest sich so leichter und
laesst sich in `sh -c '...'` einbetten.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass

import asyncssh

USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
"""Benutzer auf dem Server (Linux-Namensregel). Strenger als der Benutzername, den Nodvard Deck
fuer Windows-Zugaenge speichern darf: hier wird er in ein Shell-Skript eingesetzt."""
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


class SetupError(ValueError):
    """Ein Wert taugt nicht fuer den Einrichtungsbefehl. Der Text nennt den Wert nie."""


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
    """Zweite Sicherung fuer einen Wert, der in doppelte Anfuehrungszeichen kommt: alles
    ausser sichtbarem ASCII und jedes Zeichen, das die Shell dort deuten wuerde, wird
    abgelehnt (statt maskiert) -- die Werte sind vorher schon streng geprueft."""
    if any(ord(c) < 32 or ord(c) > 126 or c in _FORBIDDEN_IN_DOUBLE_QUOTES for c in value):
        raise SetupError(_KEY_ERROR)
    return value


def key_comment(host_name: str) -> str:
    """Kommentar des erzeugten Schluessels: `nodvard@<Kurzname>`, auf sichere Zeichen
    reduziert (Server, die eine Erweiterung entdeckt hat, koennen beliebige Namen tragen).

    Aeltere Schluessel tragen auf den Servern noch `lattice@<Kurzname>`. Der Kommentar wird bei
    jedem Aufruf neu abgeleitet und gehoert nicht zur Anmeldung; damit der Einrichtungsbefehl
    einen alten Eintrag trotzdem erkennt und nicht doppelt anlegt, sucht er nur nach Art und
    Schluesseltext (siehe `build_setup_script`)."""
    return "nodvard@" + re.sub(r"[^A-Za-z0-9_.-]", "-", host_name)[:64]


@dataclass(frozen=True)
class PublicKeyInfo:
    public_key: str
    """`<Art> <Schluessel> <Kommentar>` in einer Zeile."""
    fingerprint: str
    """`SHA256:...` wie bei `ssh-keygen -l`."""


def derive_public_key(private_key: str, *, comment: str) -> PublicKeyInfo:
    """Der oeffentliche Schluessel zu einem privaten. Der Kommentar ist IMMER `comment` --
    der im privaten Schluessel eingetragene (bei einem eingefuegten Schluessel frei waehlbar)
    wird nie uebernommen. Fehler nennen nie Schluesselmaterial."""
    try:
        key = asyncssh.import_private_key(private_key)
        line = key.convert_to_public().export_public_key("openssh").decode("ascii")
        fingerprint = key.get_fingerprint()
        alg, blob = line.split()[:2]
    except Exception:  # noqa: BLE001 - egal was asyncssh wirft: nie den Text weitergeben
        raise SetupError("Der gespeicherte Schlüssel lässt sich nicht lesen.") from None
    return PublicKeyInfo(public_key=_check_public_key(f"{alg} {blob} {comment}"), fingerprint=fingerprint)


def build_setup_script(
    user: str, public_key: str, *, groups: Iterable[str] = (), sudo: bool = False
) -> tuple[str, str]:
    """`(skript, einzeiler)`. `skript` ist zum Lesen (eine Anweisung je Zeile); `einzeiler` ist der
    Befehl zum Einfuegen: als root direkt, sonst ueber `sudo`, als EIN Argument fuer `sh -c`.

    `groups` nur fuer Benutzer ausser root (root braucht sie nicht), `sudo` ebenso. Ein
    ungueltiger Wert ergibt `SetupError`."""
    user = _check_user(user)
    key = _check_public_key(public_key)
    wanted: list[str] = []
    for group in groups:
        group = _check_group(group)
        if group not in wanted:
            wanted.append(group)
    is_root = user == "root"

    # `B` = Art und Schluesseltext ohne Kommentar: ein schon eingetragener Schluessel wird auch dann erkannt,
    # wenn er mit einem anderen Kommentar (frueher `lattice@...`) auf dem Server steht.
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
            # dort scheitert chmod -- der Link bleibt unberuehrt.
            'install -d -m 700 -o "$U" -g "$G" "$H/.ssh"; A="$H/.ssh/authorized_keys"; [ -e "$A" ] || touch "$A"',
            # Fehlt das Zeilenende der letzten Zeile, wuerde der neue Eintrag an sie gehaengt.
            '[ ! -s "$A" ] || [ -z "$(tail -c1 "$A")" ] || echo >> "$A"',
            # `restrict,pty`: kein Weiterleiten, kein Agent; ein Terminal (Terminal, `docker logs -f`),
            # Befehle und SFTP gehen weiter.
            'grep -qF -- "$B" "$A" || echo "restrict,pty $K" >> "$A"',
            '[ -L "$A" ] || { chown "$U:$G" "$A"; chmod 600 "$A"; }',
        ]
    else:
        # Alles an ~/.ssh und authorized_keys laeuft ALS der Benutzer (R): so kann ein Link dort
        # (auf /root/.ssh, /etc, eine sudoers-Datei) dem Benutzer keine Rechte an fremden Dateien
        # verschaffen -- weder per chown noch durch Anhaengen als root. Zusaetzlich bricht das
        # Skript bei einem Link ab, statt ihm zu folgen.
        lines += [
            'R() { if command -v runuser >/dev/null 2>&1; then runuser -u "$U" -- "$@"; '
            'else su -s /bin/sh "$U" -c "exec \\"\\$@\\"" -- sh "$@"; fi; }',
            'S="$H/.ssh"; A="$S/authorized_keys"',
            '[ ! -L "$S" ] || { echo "FEHLER: $S ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            '[ ! -L "$A" ] || { echo "FEHLER: $A ist ein Link und wird nicht angefasst. Bitte selbst prüfen."; exit 1; }',
            'R mkdir -p -m 700 "$S"; R chmod 700 "$S"',
            # Fehlt das Zeilenende der letzten Zeile, wuerde der neue Eintrag an sie gehaengt.
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
            # Neue Regel: /etc/sudoers.d/nodvard-<Benutzer>. Eine aeltere Regel lattice-<Benutzer> wird erst nach
            # erfolgreicher Pruefung der neuen entfernt, und nur, wenn sie Byte fuer Byte die Regel ist, die dieser Befehl
            # frueher selbst angelegt hat (`cmp` gegen die neue Datei). Alles andere bleibt stehen (Hinweis).
            # Bei einem Fehler wird nichts entfernt, und `visudo -c -q` muss am Ende gelten.
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
    if sudo and not is_root:
        lines.append('[ -z "$F" ] || { echo "Nicht alles hat geklappt – bitte die Meldungen oben lesen."; exit 1; }')
    lines.append('echo "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken."')

    script = "\n".join(lines)
    one_liner = f'S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c {shlex.quote("; ".join(lines))}'
    return script, one_liner


def setup_notes(user: str, *, groups: Iterable[str], sudo: bool) -> list[str]:
    """Hinweise zum Befehl, in einfachem Deutsch."""
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
            "Eine enger begrenzte sudo-Regel funktioniert mit Nodvard Deck nicht, weil es root-Befehle über "
            "„sudo sh -c“ startet. Darum bekommt nur dieser eine Benutzer die Rechte, und er kann sich "
            "nur mit dem Schlüssel anmelden."
        )
    if groups:
        notes.append(
            "Neue Gruppen gelten erst bei einer neuen Anmeldung – „Verbindung prüfen“ sorgt dafür, "
            "dass Nodvard Deck danach neu verbindet."
        )
    return notes
